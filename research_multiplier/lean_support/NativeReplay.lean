import RMProofData

open Lean Meta

namespace RMNativeReplay

private def expected : Name := `RMTheory.expected
private def axiomAllowed (name : Name) : Bool :=
  #[`Classical.choice, `propext, `Quot.sound].contains name

private def fromExcept (result : Except String α) : IO α :=
  match result with
  | .ok value => pure value
  | .error message => throw (IO.userError message)

private def moduleOf (env : Environment) (name : Name) : Name :=
  (env.getModuleIdxFor? name).map (env.header.moduleNames[·]!) |>.getD .anonymous

private def render (env : Environment) (value : Expr) : IO String := do
  let options := ({} : Options).setBool `pp.fullNames true
  let context : Core.Context := { fileName := "<native replay>", fileMap := FileMap.ofString "", options }
  let (formatted, _, _) ← (Meta.ppExpr value).toIO context { env }
  return formatted.pretty 100

private structure Witness where
  proof : Expr
  type : Expr

private structure Approved where
  versionId : String
  decl : Name
  witness : Witness

private def hygienic (env : Environment) (names : NameSet) : Bool :=
  names.toArray.all fun name =>
    match env.checked.get.find? name with
    | some info => !info.isUnsafe && !info.isPartial && (!info.isAxiom || axiomAllowed name)
    | none => false

private def approvedFacts (env : Environment) : IO (Array Approved) := do
  let contains ← IO.FS.readFile "approved.tsv"
  let mut facts := #[]
  let mut seen : NameSet := {}
  for line in contains.splitOn "\n" do
    if !line.isEmpty then
      let [versionId, source, reviewed] := line.splitOn "\t" |
        throw (IO.userError "Invalid approved Lean declaration mapping.")
      unless !versionId.isEmpty && !source.isEmpty &&
          reviewed == "RMApproved.basis_" ++ versionId.replace "-" "" do
        throw (IO.userError "Invalid approved declaration during native replay.")
      let name := source.toName
      if seen.contains name then
        throw (IO.userError "An approved Lean declaration is duplicated.")
      seen := seen.insert name
      if let (some (.defnInfo proposition), some info) :=
          (env.checked.get.find? reviewed.toName, env.checked.get.find? name) then
        if moduleOf env reviewed.toName == `RMApproved &&
            proposition.levelParams.isEmpty && proposition.type == .sort .zero &&
            info.levelParams.isEmpty && Kernel.isDefEqGuarded env {} info.type proposition.value &&
            (info.value? true).any (RMProofData.kernelChecks env info.type) then
          let dependencies ← fromExcept (RMProofData.closure env true [name, reviewed.toName])
          if hygienic env dependencies then
            facts := facts.push { versionId, decl := name, witness := {proof := mkConst name, type := info.type} }
  return facts

private def projectFacts (facts : Array Witness) : MetaM (Array Witness) := do
  let mut result := facts
  let mut frontier := facts
  for _ in [:8] do
    let mut extra := #[]
    for fact in frontier do
      if result.size + extra.size + 2 > facts.size + 256 then break
      let reduced ← whnf fact.type
      let args := reduced.getAppArgs
      if reduced.isAppOfArity `And 2 then
        extra := extra.push {
          proof := mkApp3 (mkConst `And.left) args[0]! args[1]! fact.proof, type := args[0]!
        }
        extra := extra.push {
          proof := mkApp3 (mkConst `And.right) args[0]! args[1]! fact.proof, type := args[1]!
        }
    if extra.isEmpty then break
    result := result ++ extra
    frontier := extra
  return result

-- Deterministic logical search over the closed reviewed basis and local
-- hypotheses only. No tactic that searches unrelated imported declarations.
private abbrev Search := StateT Nat MetaM

private partial def solve (facts : Array Witness) (type : Expr) (fuel : Nat) : Search (Option Expr) := do
  let budget ← get
  if budget == 0 then
    return none
  set (budget - 1)
  for fact in facts do
    if ← isDefEqGuarded fact.type type then
      return some fact.proof
  let reduced ← whnf type
  if reduced.isConstOf `True then
    return some (mkConst `True.intro)
  if reduced.isAppOfArity `Eq 3 then
    let args := reduced.getAppArgs
    if ← isDefEqGuarded args[1]! args[2]! then
      return some (← mkEqRefl args[1]!)
  let succ + 1 := fuel | return none
  let args := reduced.getAppArgs
  if reduced.isAppOfArity `And 2 then
    if let some left ← solve facts args[0]! succ then
      if let some right ← solve facts args[1]! succ then
        return some (mkAppN (mkConst `And.intro) #[args[0]!, args[1]!, left, right])
  if reduced.isAppOfArity `Or 2 then
    if let some left ← solve facts args[0]! succ then
      return some (mkApp3 (mkConst `Or.inl) args[0]! args[1]! left)
    if let some right ← solve facts args[1]! succ then
      return some (mkApp3 (mkConst `Or.inr) args[0]! args[1]! right)
  for fact in facts do
    let before ← Meta.saveState
    let mut arguments := #[]
    let mut outcome := fact.type
    for _ in [:8] do
      let .forallE _ domain body binder ← whnf outcome | break
      -- Kernel unification may determine ordinary and implicit arguments.
      -- Never invoke a typeclass search or guess an unconstrained term.
      if binder.isInstImplicit then break
      let argument ← mkFreshExprMVar domain
      arguments := arguments.push argument
      outcome := body.instantiate1 argument
      let beforeMatch ← Meta.saveState
      if ← isDefEqGuarded outcome type then
        for arg in arguments do
          if !(← arg.mvarId!.isAssigned) then
            let requirement ← instantiateMVars (← inferType arg)
            if requirement.hasMVar || !(← isProp requirement) then break
            let some proof ← solve facts requirement succ | break
            if !(← isDefEqGuarded arg proof) then break
        let application ← instantiateMVars (mkAppN fact.proof arguments)
        if !application.hasMVar then
          before.restore
          return some application
      beforeMatch.restore
    before.restore
  return none

private def getWitness (facts : Array Witness) (type : Expr) : MetaM (Option Expr) :=
  (·.1) <$> (solve facts type 5).run 256

private def sourcePrerequisites (type proof : Expr) (requirements : Array Json)
    (premiseCount : Nat) (facts : Array Witness) : MetaM (Except (Array Json) Witness) := do
  let mut type := type
  let mut proof := proof
  let mut missing := #[]
  for index in [:requirements.size + premiseCount] do
    let .forallE _ domain body _ ← whnf type |
      throwError "The native theorem artifact has the wrong number of reviewed premises."
    if let some witness ← getWitness facts domain then
      type := body.instantiate1 witness
      proof := mkApp proof witness
    else
      let phrase ← ppExpr domain
      let fields := if let some requirement := requirements[index]? then
        [("requirement", requirement), ("kind", toJson "approved_theorem")]
      else
        [("index", toJson (index - requirements.size + 1)), ("kind", toJson "frozen_premise")]
      missing := missing.push (Json.mkObj (fields ++ [("proposition", toJson (phrase.pretty 100))]))
      if body.hasLooseBVar 0 then
        return .error missing
      type := body.instantiate1 (mkConst `True.intro)
  return if missing.isEmpty then .ok {proof, type} else .error missing

private inductive Result where
  | replayed (proof : Expr)
  | missing (requirements : Array Json)
  | destinationNotDerived (consequent : String)

private def applyOriginal (target sourceType proof : Expr) (requirements : Array Json)
    (sourceCount targetCount : Nat) (approved : Array Approved) : MetaM Result :=
  forallBoundedTelescope target (some targetCount) fun variables outcome => do
    unless variables.size == targetCount do
      throwError "The frozen destination has the wrong number of reviewed premises."
    let facts ← variables.mapM (fun proof => do pure { proof, type := ← inferType proof : Witness })
    let facts ← projectFacts (facts ++ approved.map (·.witness))
    let applied ← sourcePrerequisites sourceType proof requirements sourceCount facts
    match applied with
    | .error missing => return .missing missing
    | .ok witness =>
      withLetDecl `replayed witness.type witness.proof fun source => do
        let all ← projectFacts (#[{proof := source, type := witness.type}] ++ facts)
        if let some result ← getWitness all outcome then
          if result.containsFVar source.fvarId! then
            let value ← mkLetFVars #[source] result
            return .replayed (← mkLambdaFVars variables value)
        return .destinationNotDerived ((← ppExpr witness.type).pretty 100)

private partial def binderOccurrence (value : Expr) (used : Array Bool := #[]) : Array Bool :=
  match value with
  | .lam _ _ body _ => binderOccurrence body (used.push (body.hasLooseBVar 0))
  | .mdata _ body => binderOccurrence body used
  | _ => used

private def reportFailure (expectedType status : String) (details : Array Json := #[])
    (rechecked := false) : IO UInt32 := do
  IO.println (Json.mkObj [
    ("accepted", toJson false), ("compiled", toJson true), ("kernel_rechecked", toJson rechecked),
    ("scope_matches", toJson false), ("expected_type", toJson expectedType), ("actual_type", Json.null),
    ("status", toJson status), ("missing", toJson details)]).compress
  return 1

private def outputAudit (env : Environment) (goal proof : Expr) (expectedType : String)
    (approved : Array Approved) (scopeMatches := true) (references : Array Name := #[])
    (unapproved : Array Name := #[]) (inlineLocal : Name → Bool := fun _ => false) : IO UInt32 := do
  let direct := RMProofData.constants proof
  let names ← fromExcept (RMProofData.closure env true ((direct ++ RMProofData.constants goal).toList))
  let checked := RMProofData.kernelChecks env goal proof
  let ordered := names.toArray.qsort (fun left right => left.toString < right.toString)
  let mut axioms := #[]
  let mut unsafeNames := #[]
  let mut external := #[]
  for name in ordered do
    let some info := env.checked.get.find? name | throw (IO.userError "A replay dependency is unresolved.")
    if info.isAxiom then
      axioms := axioms.push name
    if info.isUnsafe || info.isPartial then
      unsafeNames := unsafeNames.push name
    let origin := moduleOf env name
    if !origin.isAnonymous && ![`Init, `Lean, `Std].any (·.isPrefixOf origin) then
      external := external.push (Json.mkObj [("declaration", toJson name.toString), ("module", toJson origin.toString)])
  let allowed := axioms.filter (!axiomAllowed ·)
  let accepted := checked && scopeMatches && allowed.isEmpty && unsafeNames.isEmpty && unapproved.isEmpty
  let used := approved.filter (fun entry => names.contains entry.decl)
  let versions := used.map (·.versionId) |>.qsort (· < ·)
  let directVersions := (used.filter (fun entry => direct.contains entry.decl)).map (·.versionId) |>.qsort (· < ·)
  let portable :=
    if accepted then
      match RMProofData.exportProof env goal proof inlineLocal (used.map fun fact => (fact.versionId, fact.decl)) with
      | .ok document => [("native_proof", document)]
      | .error reason => [("native_proof_unavailable", toJson reason)]
    else []
  let asJson := fun (values : Array Name) => toJson (values.map Name.toString |>.qsort (· < ·))
  let report := Json.mkObj (portable ++ [
    ("accepted", toJson accepted), ("compiled", toJson true), ("kernel_rechecked", toJson checked),
    ("scope_matches", toJson (checked && scopeMatches)), ("expected_type", toJson expectedType), ("actual_type", toJson expectedType),
    ("basis_version_ids", toJson versions), ("expanded_basis_version_ids", toJson versions),
    ("direct_basis_version_ids", toJson directVersions),
    ("dependencies", Json.mkObj [
      ("axioms", asJson axioms), ("unauthorized_axioms", asJson allowed),
      ("unsafe", asJson unsafeNames), ("unapproved", asJson unapproved),
      ("references", asJson (used.map (·.decl))), ("constants", asJson ordered),
      ("basis_aliases", asJson references),
      ("direct_constants", asJson direct), ("external_constants", toJson external),
      ("leading_binder_occurrence", toJson (binderOccurrence proof))])])
  IO.println report.compress
  return if accepted then 0 else 1

private def decodeArchive (path : System.FilePath) : IO (Expr × Expr × Array Json × Array Json) := do
  let doc ← fromExcept (Json.parse (← IO.FS.readFile path))
  let format ← fromExcept (doc.getObjVal? "format_version" >>= Json.getNat?)
  unless format == 1 do
    throw (IO.userError "Unsupported portable Lean proof format.")
  let reqs ← fromExcept (doc.getObjVal? "requirements" >>= Json.getArr?)
  if reqs.size > 128 then
    throw (IO.userError "Too many reviewed theorem prerequisites for automatic native replay.")
  let signatures ← fromExcept (doc.getObjVal? "declarations" >>= Json.getArr?)
  let type ← fromExcept (doc.getObjVal? "type" >>= RMProofData.decodeExpr)
  let proof ← fromExcept (doc.getObjVal? "proof" >>= RMProofData.decodeExpr)
  return (type, proof, reqs, signatures)

private def closedSource (env : Environment) (type proof : Expr) : IO Bool := do
  let sourceNames ← fromExcept (RMProofData.closure env true
    ((RMProofData.constants type ++ RMProofData.constants proof).toList))
  return RMProofData.kernelChecks env type proof && hygienic env sourceNames

private def blocked (kind : String) (details : Array Json := #[]) : MetaM α :=
  throwError m!"RM_COMPOSITION_BLOCKED {(Json.mkObj [("kind", toJson kind), ("details", toJson details)]).compress}"

def composeProof (goal : Expr) (archive : System.FilePath) (sourceCount targetCount : Nat) : MetaM Expr := do
  if sourceCount > 128 || targetCount > 128 then
    throwError "Too many approved premises for native theorem composition."
  let env ← getEnv
  let (type, proof, reqs, signatures) ← decodeArchive archive
  let changes ← fromExcept (RMProofData.declarationChanges env signatures)
  if !changes.isEmpty then blocked "dependencies_need_review" changes
  unless ← closedSource env type proof do blocked "original_proof_rejected"
  let approved ← approvedFacts env
  match ← applyOriginal goal type proof reqs sourceCount targetCount approved with
  | .replayed value => return value
  | .missing details => blocked "proof_search_blocked" details
  | .destinationNotDerived consequent =>
    blocked "intermediate_not_derived" #[Json.mkObj [("source_consequent", toJson consequent)]]

private partial def moduleClosure (env : Environment) (remaining : List Name)
    (seen : NameSet := {}) : NameSet :=
  match remaining with
  | [] => seen
  | name :: rest =>
    if seen.contains name then moduleClosure env rest seen
    else
      let imports := (env.getModuleIdx? name).map (env.header.moduleData[·]!.imports) |>.getD #[]
      moduleClosure env (imports.toList.map (·.module) ++ rest) (seen.insert name)

def compositionAudit (modules : List Name) : IO UInt32 := do
  let env ← importModules #[{module := `Lean}, {module := `RMExpected}, {module := `RMApproved},
    {module := `RMCandidate}] {}
  let some (.defnInfo goal) := env.checked.get.find? expected |
    throw (IO.userError "The reviewed composition goal is missing.")
  let some (.thmInfo candidate) := env.checked.get.find? `RMTheory.Proof.checked |
    throw (IO.userError "The generated composition proof is missing.")
  unless moduleOf env expected == `RMExpected && goal.levelParams.isEmpty && goal.type == .sort .zero &&
    moduleOf env `RMTheory.Proof.checked == `RMCandidate do
    throw (IO.userError "The composition proof did not use the trusted goal and candidate modules.")
  let inlineLocal := fun name => moduleOf env name == `RMCandidate
  let roots := RMProofData.constants goal.value ++ RMProofData.constants candidate.type
  let names ← fromExcept (RMProofData.closure env true (roots ++ RMProofData.constants candidate.value).toList)
  let aliases := names.toArray.filter fun name => inlineLocal name &&
    name.toString.startsWith "RMTheory.Proof.basis_"
  let allowedExternal ← fromExcept (RMProofData.closure env true (roots ++ aliases).toList)
  let allowedModules := moduleClosure env modules
  let unapproved := names.toArray.filter fun name =>
    let origin := moduleOf env name
    ![`Init, `Lean, `Std, `RMExpected, `RMCandidate].any (·.isPrefixOf origin) &&
      (!allowedModules.contains origin || !allowedExternal.contains name)
  let scopeMatches := candidate.levelParams.isEmpty &&
    Kernel.isDefEqGuarded env {} candidate.type goal.value
  outputAudit env goal.value candidate.value (← render env goal.value) (← approvedFacts env)
    scopeMatches aliases unapproved inlineLocal

def replay (sourceCount targetCount : Nat) : IO UInt32 := do
  if sourceCount > 128 || targetCount > 128 then
    throw (IO.userError "Too many frozen Lean premises for automatic native replay.")
  let (type, proof, reqs, signatures) ← decodeArchive "native-proof.json"
  let env ← importModules #[{module := `Lean}, {module := `RMExpected}, {module := `RMApproved}] {}
  let some (.defnInfo goal) := env.checked.get.find? expected |
    throw (IO.userError "The reviewed destination goal is missing.")
  unless moduleOf env expected == `RMExpected && goal.levelParams.isEmpty && goal.type == .sort .zero do
    throw (IO.userError "The reviewed destination goal is not a closed proposition.")
  let expectedType ← render env goal.value
  let changes ← fromExcept (RMProofData.declarationChanges env signatures)
  if !changes.isEmpty then
    return ← reportFailure expectedType "dependencies_need_review" changes
  unless ← closedSource env type proof do
    return ← reportFailure expectedType "original_proof_rejected"
  let approved ← approvedFacts env
  let options := ({} : Options).setBool `pp.fullNames true |>.setNat `maxHeartbeats 500000
  let context : Core.Context := {fileName := "<native replay>", fileMap := FileMap.ofString "", options}
  let (result, _, _) ← (applyOriginal goal.value type proof reqs sourceCount targetCount approved).toIO context {env}
  match result with
  | .missing details => reportFailure expectedType "missing_requirements" details true
  | .destinationNotDerived type =>
    reportFailure expectedType "destination_not_derived" #[Json.mkObj [("source_consequent", toJson type)]] true
  | .replayed value => outputAudit env goal.value value expectedType approved

end RMNativeReplay

def main (args : List String) : IO UInt32 := do
  initSearchPath (← findSysroot)
  match args with
  | ["replay", source, target] =>
    let (some sourceCount, some targetCount) := (source.toNat?, target.toNat?) |
      throw (IO.userError "Native replay expects source and destination premise counts.")
    RMNativeReplay.replay sourceCount targetCount
  | ["composition-audit", modules] =>
    RMNativeReplay.compositionAudit <|
      if modules.isEmpty then [] else (modules.splitOn ",").map String.toName
  | _ => throw (IO.userError "Expected replay SOURCE_PREMISE_COUNT DESTINATION_PREMISE_COUNT.")
