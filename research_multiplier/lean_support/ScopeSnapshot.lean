import Lean

open Lean

namespace RMScopeSnapshot

private def expected : Name := `RMTheory.expected
private def acceptedAxioms : NameSet := [`Classical.choice, `propext, `Quot.sound].foldl (·.insert ·) {}
private def nameFromText (text : String) : Name :=
  (text.splitOn ".").foldl Name.str .anonymous

private def moduleOf (env : Environment) (name : Name) : Name :=
  (env.getModuleIdxFor? name).map (env.header.moduleNames[·]!) |>.getD .anonymous

private abbrev Encoding := StateT (Array Json) (Except String)
private def token (value : Json) : Encoding Unit := modify (·.push value)
private def tag (value : String) : Encoding Unit := token (toJson value)

private partial def encodeName : Name → Encoding Unit
  | .anonymous => tag "anonymous"
  | .str name part => do
    tag "str"
    encodeName name
    token (toJson part)
  | .num name part => do
    tag "num"
    encodeName name
    token (toJson part)

private partial def encodeLevel (parameters : List Name) : Level → Encoding Unit
  | .zero => tag "zero"
  | .succ level => do
    tag "succ"
    encodeLevel parameters level
  | .max first second => do
    tag "max"
    encodeLevel parameters first
    encodeLevel parameters second
  | .imax first second => do
    tag "imax"
    encodeLevel parameters first
    encodeLevel parameters second
  | .param name => do
    let some index := parameters.idxOf? name | throw "A universe parameter is outside its declaration."
    tag "parameter"
    token (toJson index)
  | .mvar _ => throw "A reviewed declaration has an unresolved universe."

private def binderKind : BinderInfo → String
  | .default => "explicit"
  | .implicit => "implicit"
  | .strictImplicit => "strictImplicit"
  | .instImplicit => "instance"

-- The kernel already uses de Bruijn indices. Omit binder spelling, metadata
-- and implementation-only flags. Keep all kernel arguments and universe data.
private partial def encodeExpr (parameters : List Name) (expression : Expr) : Encoding Unit := do
  let emit := encodeExpr parameters
  match expression with
  | .bvar index =>
    tag "bound"
    token (toJson index)
  | .fvar _ | .mvar _ => throw "A reviewed declaration is not closed."
  | .sort level =>
    tag "sort"
    encodeLevel parameters level
  | .const name levels =>
    tag "constant"
    encodeName name
    token (toJson levels.length)
    for level in levels do
      encodeLevel parameters level
  | .app fn arg =>
    tag "app"
    emit fn
    emit arg
  | .lam _ domain body kind | .forallE _ domain body kind =>
    tag (if expression.isLambda then "lambda" else "forall")
    tag (binderKind kind)
    emit domain
    emit body
  | .letE _ type value body _ =>
    tag "let"
    emit type
    emit value
    emit body
  | .lit (.natVal value) =>
    tag "nat"
    token (toJson value)
  | .lit (.strVal value) =>
    tag "string"
    token (toJson value)
  | .mdata _ body => emit body
  | .proj name index value =>
    tag "projection"
    encodeName name
    token (toJson index)
    emit value

private def canonical (parameters : List Name) (expression : Expr) : Except String Json := do
  let (_, tokens) ← (encodeExpr parameters expression).run #[]
  return toJson (Json.arr tokens).compress

private def constants (expression : Expr) : Array Name :=
  runST fun world => do
    let names ← ST.mkRef (σ := world) (m := ST world) expression.getUsedConstants
    expression.forEach (ω := world) (m := ST world) fun
      | .proj name _ _ => names.modify (·.push name)
      | _ => pure ()
    names.get

private def isProof (env : Environment) (info : ConstantInfo) : Bool :=
  let levels := info.levelParams.map fun _ => Level.succ .zero
  let type := info.type.instantiateLevelParams info.levelParams levels
  match Kernel.check env {} type with
  | .ok inferred => Kernel.isDefEqGuarded env {} inferred (.sort .zero)
  | .error _ => false

-- A theorem's implementation establishes trust, but its meaning is its type.
-- Computational definitions supply meaning through their bodies.
private def semanticValue (env : Environment) (info : ConstantInfo) : Option Expr :=
  match info with
  | .defnInfo value => if isProof env info then none else some value.value
  | .opaqueInfo value => if isProof env info then none else some value.value
  | _ => none

private def dependencies (env : Environment) (allValues : Bool) (info : ConstantInfo) : Array Name :=
  let value := if allValues then info.value? true else semanticValue env info
  let generated := match info with
    | .inductInfo data => data.all.toArray ++ data.ctors.toArray
    | .ctorInfo data => #[data.induct]
    | .recInfo data => data.all.toArray ++ data.rules.toArray.flatMap (fun rule =>
        #[rule.ctor] ++ constants rule.rhs)
    | _ => #[]
  constants info.type ++ ((value.map constants).getD #[]) ++ generated

private partial def closure (env : Environment) (allValues : Bool) (pending : List Name)
    (seen : NameSet := {}) : Except String NameSet := do
  match pending with
  | [] => pure seen
  | name :: rest =>
    if seen.contains name then closure env allValues rest seen
    else
      let some info := env.checked.get.find? name | throw s!"Lean could not resolve {name}."
      closure env allValues ((dependencies env allValues info).toList ++ rest) (seen.insert name)

private def kind (info : ConstantInfo) : String :=
  match info with
  | .axiomInfo _ => "axiom"
  | .defnInfo _ => "definition"
  | .thmInfo _ => "theorem"
  | .opaqueInfo _ => "opaque"
  | .quotInfo _ => "quotient"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"

private def declaration (env : Environment) (name : Name) : Except String Json := do
  let some info := env.checked.get.find? name | throw s!"Lean could not resolve {name}."
  let pack := canonical info.levelParams
  let metadata ← match info with
    | .inductInfo data => pure #[toJson data.numParams, toJson data.numIndices, toJson data.all,
        toJson data.ctors, toJson data.numNested, toJson data.isRec, toJson data.isReflexive]
    | .ctorInfo data => pure #[toJson data.induct, toJson data.cidx, toJson data.numParams, toJson data.numFields]
    | .recInfo data => do
      let rules ← data.rules.mapM fun rule => do
        let rhs ← pack rule.rhs
        return Json.arr #[toJson rule.ctor, toJson rule.nfields, rhs]
      pure #[toJson data.all, toJson data.numParams, toJson data.numIndices, toJson data.numMotives,
        toJson data.numMinors, toJson data.k, toJson rules]
    | .quotInfo data =>
      let name := match data.kind with
        | .type => "type"
        | .ctor => "constructor"
        | .lift => "lift"
        | .ind => "induction"
      pure #[toJson name]
    | _ => pure #[]
  let value ← match semanticValue env info with
    | some expression => pack expression
    | none => pure Json.null
  let type ← pack info.type
  return Json.arr (#[toJson (moduleOf env name), toJson (kind info), toJson info.levelParams.length, type,
    toJson info.isUnsafe, toJson info.isPartial, value] ++ metadata)

private def sortedNames (names : NameSet) : Array Name :=
  names.toArray.qsort (fun first second => first.toString < second.toString)

private structure Scope where
  meaning : NameSet
  axioms : NameSet
  unsafeNames : NameSet

private def scope (env : Environment) (semantic trust : List Name) : Except String Scope := do
  let meaning ← closure env false semantic
  let reachable ← closure env true trust
  let (axioms, unsafeNames) := reachable.toArray.foldl (fun (axioms, unsafeNames) name =>
    match env.checked.get.find? name with
    | some info => (if info.isAxiom then axioms.insert name else axioms,
                     if info.isUnsafe || info.isPartial then unsafeNames.insert name else unsafeNames)
    | none => (axioms, unsafeNames)) (({} : NameSet), ({} : NameSet))
  return { meaning, axioms, unsafeNames }

private def scopeFields (result : Scope) (trusted : Bool) : List (String × Json) :=
  [("declarations", toJson (sortedNames result.meaning)), ("axioms", toJson (sortedNames result.axioms)),
   ("trusted", toJson (trusted && result.unsafeNames.isEmpty && result.axioms.toArray.all acceptedAxioms.contains)),
   ("unsafe", toJson (sortedNames result.unsafeNames))]

private structure Approval where
  version : String
  decl : Name
  proposition : Name

private def readApprovals : IO (List Approval) := do
  let text ← IO.FS.readFile "approved.tsv"
  let mut approvals := #[]
  for line in text.splitOn "\n" do
    if !line.isEmpty then
      let [version, decl, prop] := line.splitOn "\t" |
        throw <| IO.userError "Invalid approved Lean declaration mapping."
      unless !version.isEmpty && !decl.isEmpty && prop == "RMApproved.basis_" ++ version.replace "-" "" do
        throw <| IO.userError "Invalid approved Lean declaration identifier or proposition."
      let name := nameFromText decl
      if approvals.any (fun (row : Approval) => row.version == version || row.decl == name) then
        throw <| IO.userError "Approved Lean declaration names and versions must be unique."
      approvals := approvals.push { version, decl := name, proposition := nameFromText prop }
  return approvals.toList

private def rechecks (env : Environment) (type value : Expr) : Bool :=
  match Kernel.check env {} value with
  | .ok inferred => Kernel.isDefEqGuarded env {} inferred type
  | .error _ => false

private def basisRow (env : Environment) (approved : Approval) : Except String (Json × NameSet) := do
  let some (.defnInfo proposition) := env.checked.get.find? approved.proposition |
    throw "The approved proposition is absent."
  unless moduleOf env approved.proposition == `RMApproved && proposition.levelParams.isEmpty &&
      proposition.type == .sort .zero do
    throw "The approved proposition does not have the expected type."
  let prop ← canonical [] proposition.value
  let found := env.checked.get.find? approved.decl
  let semanticRoots := (constants proposition.value).toList ++ if found.isSome then [approved.decl] else []
  let dependencies ← scope env semanticRoots (approved.proposition :: semanticRoots)
  let actual ← match found with
    | none => pure Json.null
    | some info => canonical info.levelParams info.type
  let issue := match found with
    | none => some "missing_declaration"
    | some info => if !info.levelParams.isEmpty ||
         !Kernel.isDefEqGuarded env {} info.type proposition.value then some "different_statement"
        else if !(info.value? true).any (rechecks env info.type) then some "kernel_rejected"
        else if !dependencies.axioms.toArray.all acceptedAxioms.contains then some "unauthorized_axioms"
        else if !dependencies.unsafeNames.isEmpty then some "unsafe_declarations"
        else none
  return (Json.mkObj ([("version_id", toJson approved.version), ("declaration", toJson approved.decl),
    ("expression", prop), ("actual_type", actual), ("issue", toJson issue)] ++
    scopeFields dependencies issue.isNone), dependencies.meaning)

private def snapshot (env : Environment) (approvals : List Approval) : Except String Json := do
  let some (.defnInfo goal) := env.checked.get.find? expected | throw "The frozen Lean goal is absent."
  unless moduleOf env expected == `RMExpected && goal.levelParams.isEmpty && goal.type == .sort .zero do
    throw "The frozen goal does not have the expected type."
  let goalScope ← scope env (constants goal.value).toList [expected]
  let goalExpr ← canonical [] goal.value
  let goalRow := Json.mkObj (("expression", goalExpr) :: scopeFields goalScope (rechecks env goal.type goal.value))
  let basis ← approvals.mapM (basisRow env)
  let allMeaning := basis.foldl (fun seen (_, meanings) => meanings.toArray.foldl (·.insert ·) seen) goalScope.meaning
  let declarations ← (sortedNames allMeaning).toList.mapM fun name => do
    let row ← declaration env name
    return (name.toString, row)
  return Json.mkObj [("format_version", toJson (1 : Nat)), ("goal", goalRow),
    ("basis", toJson (basis.map Prod.fst)), ("declarations", Json.mkObj declarations)]

def main : IO Unit := do
  initSearchPath (← findSysroot)
  let env ← importModules #[{ module := `Lean }, { module := `RMExpected }, { module := `RMApproved }] {}
  match snapshot env (← readApprovals) with
  | .error error => throw <| IO.userError error
  | .ok result => IO.println result.compress

end RMScopeSnapshot

def main := RMScopeSnapshot.main
