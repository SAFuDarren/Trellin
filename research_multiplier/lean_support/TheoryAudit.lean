import Lean

open Lean

namespace RMTheoryAudit

private def anchors : Array Name := #[`RMTheory.expected, `RMTheory.Proof.checked]
private def axiomsAllowed : Array Name := #[`Classical.choice, `propext, `Quot.sound]

private def moduleNames (text : String) : List Name :=
  (text.splitOn ",").map fun name => (name.splitOn ".").foldl Name.str .anonymous

-- The approved surface terms use a fixed subset of Core syntax. In particular,
-- additional term elaborators can read files or execute code before the kernel.
private def propositionSyntax : Array Name := #[
  `null, `num,
  `Lean.Parser.Term.app, `Lean.Parser.Term.paren, `Lean.Parser.Term.typeAscription,
  `Lean.Parser.Term.typeSpec, `Lean.Parser.Term.prop, `Lean.Parser.Term.type,
  `Lean.Parser.Term.forall, `Lean.Parser.Term.arrow,
  `Lean.Parser.Term.explicitBinder, `Lean.Parser.Term.implicitBinder,
  `Lean.explicitBinders, `Lean.unbracketedExplicitBinders, `Lean.binderIdent,
  `«term∃_,_», `«term_∧_», `«term_∨_», `«term¬_», `«term_↔_»,
  `«term_=_», `«term_≠_», `«term_<_», `«term_≤_», `«term_>_», `«term_≥_»,
  `«term_+_», `«term_-_», `«term-_», `«term_*_», `«term_/_», `«term_%_», `«term_^_»
]

private partial def pureTerm : Syntax → Bool
  | .node _ kind args => propositionSyntax.contains kind && args.all pureTerm
  | .ident .. | .atom .. => true
  | .missing => false

private unsafe def validate (files : List String) (modules : List Name := []) : IO Unit := do
  enableInitializersExecution
  let env ← importModules ((`Lean :: modules).toArray.map ({ module := · })) {} (loadExts := true)
  for file in files do
    let source ← IO.FS.readFile file
    match Parser.runParserCategory env `term source with
    | .ok term =>
      unless pureTerm term do
        throw <| IO.userError "A reviewed proposition uses unsupported Lean Core syntax."
    | .error message => throw <| IO.userError message

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

private partial def leadingBinderOccurrence (value : Expr) (used : Array Bool := #[]) : Array Bool :=
  match value with
  | .lam _ _ body _ => leadingBinderOccurrence body (used.push (body.hasLooseBVar 0))
  | .mdata _ body => leadingBinderOccurrence body used
  | _ => used

private partial def closure (env : Environment) (pending : List Name) (seen : NameSet := {}) :
    NameSet :=
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

private def render (env : Environment) (value : Expr) : IO String := do
  let options := ({} : Options).setBool `pp.fullNames true
  let context : Core.Context := { fileName := "<theory>", fileMap := FileMap.ofString "", options }
  let (formatted, _, _) ← (Meta.ppExpr value).toIO context { env }
  return formatted.pretty 100

private def describe : IO UInt32 := do
  let env ← importModules #[{ module := `Lean }, { module := `RMExpected }] {}
  let some (.defnInfo goal) := env.checked.get.find? anchors[0]! |
    throw <| IO.userError "The reviewed goal is absent."
  IO.println (Json.mkObj [("expected_type", toJson (← render env goal.value))]).compress
  return 0

private def audit (modules : List Name := []) : IO UInt32 := do
  let env ← importModules #[{ module := `Lean }, { module := `RMExpected }, { module := `RMCandidate }] {}
  unless modules.all (fun name => (env.getModuleIdx? name).isSome) do
    throw <| IO.userError "The checked environment has missing reviewed imports."
  let allowedModules := moduleClosure env modules
  let some (.defnInfo goal) := env.checked.get.find? anchors[0]! |
    throw <| IO.userError "The reviewed goal is absent."
  let some (.thmInfo proof) := env.checked.get.find? anchors[1]! |
    throw <| IO.userError "The candidate is not a theorem."
  let matchesScope := proof.levelParams.isEmpty && Kernel.isDefEqGuarded env {} proof.type goal.value
  let checked := match Kernel.check env {} proof.value with
    | .ok inferred => Kernel.isDefEqGuarded env {} inferred proof.type
    | .error _ => false
  let typeNames := constants proof.type ++ constants goal.value
  let names := (closure env (typeNames ++ constants proof.value).toList).toArray
  let moduleOf := fun name =>
    (env.getModuleIdxFor? name).map (env.header.moduleNames[·]!) |>.getD .anonymous
  let isBasis := fun name => moduleOf name == `RMCandidate && name.toString.startsWith "RMTheory.Proof.basis_"
  let referencedBasis := names.filter isBasis
  let allowedExternal := closure env (typeNames ++ referencedBasis).toList
  let mut axioms := #[]
  let mut unsafeNames := #[]
  let mut unapproved := #[]
  let mut references := #[]
  let mut external := #[]
  for name in names.qsort (fun left right => left.toString < right.toString) do
    let some info := env.checked.get.find? name |
      throw <| IO.userError s!"The kernel could not find {name}."
    let source := moduleOf name
    if info.isAxiom then axioms := axioms.push name
    if info.isUnsafe || info.isPartial then unsafeNames := unsafeNames.push name
    if isBasis name then
      references := references.push name
    else if !([ `Init, `Lean, `Std, `RMExpected, `RMCandidate ].any (·.isPrefixOf source)) then
      external := external.push (Json.mkObj [("declaration", toJson name.toString), ("module", toJson source.toString)])
      if !allowedModules.contains source || !allowedExternal.contains name then
        unapproved := unapproved.push name
  let asJson := fun (values : Array Name) => toJson (values.map Name.toString |>.qsort (· < ·))
  let unauthorized := axioms.filter (!axiomsAllowed.contains ·)
  let accepted := checked && matchesScope && unauthorized.isEmpty && unsafeNames.isEmpty && unapproved.isEmpty
  let report := Json.mkObj [
    ("accepted", toJson accepted), ("compiled", toJson true),
    ("kernel_rechecked", toJson checked), ("scope_matches", toJson matchesScope),
    ("expected_type", toJson (← render env goal.value)), ("actual_type", toJson (← render env proof.type)),
    ("dependencies", Json.mkObj [
      ("axioms", asJson axioms), ("unauthorized_axioms", asJson unauthorized),
      ("unsafe", asJson unsafeNames), ("unapproved", asJson unapproved),
      ("references", asJson references), ("constants", asJson names),
      ("direct_constants", asJson (constants proof.value)),
      ("external_constants", .arr external),
      ("leading_binder_occurrence", toJson (leadingBinderOccurrence proof.value))])]
  IO.println report.compress
  return if accepted then 0 else 1

unsafe def main (args : List String) : IO UInt32 := do
  initSearchPath (← findSysroot)
  match args with
  | "validate" :: files => validate files *> pure 0
  | "validate-imports" :: names :: files => validate files (moduleNames names) *> pure 0
  | ["audit"] => audit
  | ["audit-imports", names] => audit (moduleNames names)
  | ["describe"] => describe
  | _ => throw <| IO.userError "Expected validate, describe, or audit."

end RMTheoryAudit

unsafe def main := RMTheoryAudit.main
