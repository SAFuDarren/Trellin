import RMProofData

open Lean

namespace RMSourceAudit

private def expected : Name := `RMTheory.expected
private def axiomsAllowed : Array Name := #[`Classical.choice, `propext, `Quot.sound]
private def coreScaffolding : Array Name := #[`False.elim, `Eq.symm, `rfl]

private def dottedName (text : String) : Name :=
  (text.splitOn ".").foldl Name.str .anonymous

private def moduleOf (env : Environment) (name : Name) : Name :=
  (env.getModuleIdxFor? name).map (env.header.moduleNames[·]!) |>.getD .anonymous

private def constants (value : Expr) : Array Name :=
  runST fun world => do
    let names ← ST.mkRef (σ := world) (m := ST world) value.getUsedConstants
    value.forEach (ω := world) (m := ST world) fun
      | .proj name _ _ => names.modify (·.push name)
      | _ => pure ()
    names.get

private def dependsOn (info : ConstantInfo) : Array Name :=
  let generated := match info with
    | .inductInfo data => data.ctors.toArray
    | .ctorInfo data => #[data.induct]
    | .recInfo data => data.all.toArray ++ data.rules.toArray.flatMap (constants ·.rhs)
    | _ => #[]
  constants info.type ++ ((info.value? true).map constants |>.getD #[]) ++ generated

private partial def closure (env : Environment) (pending : List Name) (seen : NameSet := {}) : NameSet :=
  match pending with
  | [] => seen
  | name :: rest =>
    if seen.contains name then closure env rest seen
    else
      let next := (env.checked.get.find? name).map dependsOn |>.getD #[]
      closure env (next.toList ++ rest) (seen.insert name)

private partial def moduleClosure (env : Environment) (pending : List Name) (seen : NameSet := {}) : NameSet :=
  match pending with
  | [] => seen
  | name :: rest =>
    if seen.contains name then moduleClosure env rest seen
    else
      let imports := (env.getModuleIdx? name).map (env.header.moduleData[·]!.imports) |>.getD #[]
      moduleClosure env (imports.toList.map (·.module) ++ rest) (seen.insert name)

private def coreModules (env : Environment) (used : NameSet) : IO NameSet := do
  let root ← realPathNormalized (← getLibDir (← findSysroot))
  let origins := used.toArray.foldl (fun (modules : NameSet) name => modules.insert (moduleOf env name)) {}
  let mut accepted : NameSet := {}
  for origin in origins.toArray do
    if [ `Init, `Lean, `Std ].any (·.isPrefixOf origin) then
      let location ← realPathNormalized (← findOLean origin)
      if root.components.isPrefixOf location.components then
        accepted := accepted.insert origin
  return accepted

private def rechecks (env : Environment) (type value : Expr) : Bool :=
  match Kernel.check env {} value with
  | .ok inferred => Kernel.isDefEqGuarded env {} inferred type
  | .error _ => false

private partial def leadingBinderOccurrence (value : Expr) (used : Array Bool := #[]) : Array Bool :=
  match value with
  | .lam _ _ body _ => leadingBinderOccurrence body (used.push (body.hasLooseBVar 0))
  | .mdata _ body => leadingBinderOccurrence body used
  | _ => used

private def render (env : Environment) (value : Expr) : IO String := do
  let options := ({} : Options).setBool `pp.fullNames true
  let context : Core.Context := { fileName := "<proof>", fileMap := FileMap.ofString "", options }
  let (formatted, _, _) ← (Meta.ppExpr value).toIO context { env }
  return formatted.pretty 100

private structure Approval where
  id : String
  decl : Name
  proposition : Name

private def readApprovals : IO (Array Approval) := do
  let contents ← IO.FS.readFile "approved.tsv"
  let mut approvals := #[]
  for line in contents.splitOn "\n" do
    if !line.isEmpty then
      let [id, decl, proposition] := line.splitOn "\t" |
        throw <| IO.userError "Invalid approved theorem mapping."
      unless !id.isEmpty && !decl.isEmpty && proposition == "RMApproved.basis_" ++ id.replace "-" "" do
        throw <| IO.userError "An approved theorem mapping has an invalid identifier or proposition."
      let declaration := dottedName decl
      if approvals.any (fun (item : Approval) => item.id == id || item.decl == declaration) then
        throw <| IO.userError "Approved theorem identifiers and declaration names must each be unique."
      approvals := approvals.push { id, decl := declaration, proposition := dottedName proposition }
  return approvals

private def validApproval (env : Environment) (allowedModules core : NameSet) (item : Approval) : Bool :=
  match env.checked.get.find? item.proposition, env.checked.get.find? item.decl with
  | some (.defnInfo proposition), some proof =>
    let source := moduleOf env item.decl
    match proof.value? true with
    | none => false
    | some value =>
      moduleOf env item.proposition == `RMApproved && proposition.levelParams.isEmpty &&
        proposition.type == .sort .zero && (core.contains source || allowedModules.contains source) &&
        proof.levelParams.isEmpty && Kernel.isDefEqGuarded env {} proof.type proposition.value &&
        rechecks env proof.type value && (closure env [item.decl, item.proposition]).toArray.all (fun name =>
          match env.checked.get.find? name with
          | some info => !info.isUnsafe && !info.isPartial && (!info.isAxiom || axiomsAllowed.contains name)
          | none => false)
  | _, _ => false

-- Source-local proofs and their auxiliaries do not open a second route to
-- unaudited facts from a Lake project or library.
private partial def proofBoundary (env : Environment) (auxiliary : Name → Bool)
    (pending : List Name) (seen : NameSet := {}) (exposed : NameSet := {}) : NameSet :=
  match pending with
  | [] => exposed
  | name :: rest =>
    if seen.contains name then proofBoundary env auxiliary rest seen exposed
    else if auxiliary name then
      let next := (env.checked.get.find? name).map dependsOn |>.getD #[]
      proofBoundary env auxiliary (next.toList ++ rest) (seen.insert name) exposed
    else
      proofBoundary env auxiliary rest (seen.insert name) (exposed.insert name)

private def proofConstant (env : Environment) (info : ConstantInfo) : Bool :=
  match info with
  | .thmInfo _ | .opaqueInfo _ => true
  | .defnInfo data =>
    -- Nonzero universe levels preserve polymorphic computational definitions.
    let levels := data.levelParams.map fun _ => Level.succ .zero
    let type := data.type.instantiateLevelParams data.levelParams levels
    match Kernel.check env {} type with
    | .ok inferred => Kernel.isDefEqGuarded env {} inferred (.sort .zero)
    | .error _ => true
  | _ => false

private def audit (candidateModule : Name) (candidateText : String) (modules : List Name) (sourceMode : Bool) : IO UInt32 := do
  let env ← importModules #[{ module := `Lean }, { module := `RMExpected }, { module := `RMApproved },
    { module := candidateModule }] {}
  unless modules.all (fun name => (env.getModuleIdx? name).isSome) do
    throw <| IO.userError "The checked environment has missing reviewed imports."
  let allowedModules := moduleClosure env modules
  unless (sourceMode && candidateModule == `RMSubmitted) || (!sourceMode && allowedModules.contains candidateModule) do
    throw <| IO.userError "The candidate module is outside the approved checking environment."
  let some moduleIdx := env.getModuleIdx? candidateModule |
    throw <| IO.userError "The candidate module is missing."
  let localNames := env.header.moduleData[moduleIdx]!.constNames
  let some candidate := localNames.find? (·.toString == candidateText) |
    throw <| IO.userError "The candidate declaration is absent from the selected module."
  unless moduleOf env candidate == candidateModule do
    throw <| IO.userError "The candidate declaration belongs to a different module."
  let some (.thmInfo proof) := env.checked.get.find? candidate |
    throw <| IO.userError "The candidate declaration is not a Lean theorem."
  let some (.defnInfo goal) := env.checked.get.find? expected |
    throw <| IO.userError "The reviewed goal is absent."
  unless moduleOf env expected == `RMExpected && goal.levelParams.isEmpty && goal.type == .sort .zero do
    throw <| IO.userError "The reviewed goal is not the expected monomorphic proposition."
  let approvals ← readApprovals
  let checked := proof.levelParams.isEmpty && rechecks env proof.type proof.value
  let matchesScope := proof.levelParams.isEmpty && Kernel.isDefEqGuarded env {} proof.type goal.value
  let auxiliary := fun name => sourceMode && name != candidate && moduleOf env name == candidateModule
  let directNames := constants proof.value
  let actualTypeNames := constants proof.type
  let reviewedType := closure env [expected]
  let newTypeNames := actualTypeNames.filter (!reviewedType.contains ·)
  let boundary := proofBoundary env auxiliary (directNames ++ newTypeNames).toList
  let usedNames := closure env (expected :: (actualTypeNames ++ directNames).toList)
  let core ← coreModules env usedNames
  let names := usedNames.toArray
  let used := approvals.filter (fun item => usedNames.contains item.decl)
  let validated := used.filter (validApproval env allowedModules core)
  let directApprovals := validated.filter fun item => boundary.contains item.decl || actualTypeNames.contains item.decl
  let permitted := closure env (expected :: (directApprovals.map (·.decl)).toList)
  let hasApproval := fun name => validated.any (·.decl == name)
  let mut axioms := #[]
  let mut unsafeNames := #[]
  let mut unapproved := #[]
  let mut external := #[]
  for name in names.qsort (fun left right => left.toString < right.toString) do
    let some info := env.checked.get.find? name |
      throw <| IO.userError s!"The kernel could not find {name}."
    let moduleName := moduleOf env name
    if info.isAxiom then axioms := axioms.push name
    if info.isUnsafe || info.isPartial then unsafeNames := unsafeNames.push name
    if name == candidate || (used.any (·.decl == name) && !hasApproval name) then
      unapproved := unapproved.push name
    else if core.contains moduleName then
      if boundary.contains name && !hasApproval name && !coreScaffolding.contains name && proofConstant env info then
        unapproved := unapproved.push name
    else if moduleName != `RMExpected && !auxiliary name then
      external := external.push (Json.mkObj [("declaration", toJson name.toString), ("module", toJson moduleName.toString)])
      if !allowedModules.contains moduleName || !permitted.contains name ||
          (boundary.contains name && !hasApproval name && (!reviewedType.contains name || proofConstant env info)) ||
          (actualTypeNames.contains name && !reviewedType.contains name && !hasApproval name) then
        unapproved := unapproved.push name
  let asJson := fun (values : Array Name) => toJson (values.map Name.toString |>.qsort (· < ·))
  let unauthorized := axioms.filter (!axiomsAllowed.contains ·)
  let accepted := checked && matchesScope && unauthorized.isEmpty && unsafeNames.isEmpty && unapproved.isEmpty
  let directIds := (validated.filter (fun item => directNames.contains item.decl)).map (·.id) |>.qsort (· < ·)
  let expandedIds := validated.map (·.id) |>.qsort (· < ·)
  let portable :=
    if accepted then
      match RMProofData.exportProof env goal.value proof.value auxiliary (validated.map fun item => (item.id, item.decl)) with
      | .ok document => [("native_proof", document)]
      | .error reason => [("native_proof_unavailable", toJson reason)]
    else []
  let report := Json.mkObj (portable ++ [
    ("accepted", toJson accepted), ("compiled", toJson true),
    ("source_declaration", toJson candidate.toString), ("source_module", toJson candidateModule.toString),
    ("kernel_rechecked", toJson checked), ("scope_matches", toJson matchesScope),
    ("expected_type", toJson (← render env goal.value)), ("actual_type", toJson (← render env proof.type)),
    ("direct_basis_version_ids", toJson directIds), ("basis_version_ids", toJson expandedIds),
    ("expanded_basis_version_ids", toJson expandedIds),
    ("dependencies", Json.mkObj [
      ("axioms", asJson axioms), ("unauthorized_axioms", asJson unauthorized),
      ("unsafe", asJson unsafeNames), ("unapproved", asJson unapproved),
      ("references", asJson (validated.map (·.decl))), ("constants", asJson names),
      ("direct_constants", asJson directNames), ("type_constants", asJson actualTypeNames),
      ("external_constants", .arr external),
      ("leading_binder_occurrence", toJson (leadingBinderOccurrence proof.value))])])
  IO.println report.compress
  return if accepted then 0 else 1

def main (args : List String) : IO UInt32 := do
  initSearchPath (← findSysroot)
  match args with
  | ["audit", candidateModule, candidateDecl, reviewed, mode] =>
    unless mode == "source" || mode == "compiled" do
      throw <| IO.userError "Expected source or compiled proof mode."
    let modules := if reviewed.isEmpty then [] else (reviewed.splitOn ",").map dottedName
    audit (dottedName candidateModule) candidateDecl modules (mode == "source")
  | _ => throw <| IO.userError "Expected audit MODULE DECLARATION REVIEWED_MODULES MODE."

end RMSourceAudit

def main := RMSourceAudit.main
