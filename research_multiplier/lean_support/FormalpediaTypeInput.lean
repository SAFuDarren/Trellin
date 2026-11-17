import Lean

open Lean

-- Only parse exported proposition terms. Permit the reviewed Mathlib syntax
-- used by this adapter, while rejecting user commands, tactics and elaborators.
private def allowed : Array Name := #[
  `null, `num,
  `Lean.Parser.Term.app, `Lean.Parser.Term.paren, `Lean.Parser.Term.typeAscription,
  `Lean.Parser.Term.typeSpec, `Lean.Parser.Term.prop, `Lean.Parser.Term.type,
  `Lean.Parser.Term.forall, `Lean.Parser.Term.arrow,
  `Lean.Parser.Term.explicitBinder, `Lean.Parser.Term.implicitBinder,
  `Lean.explicitBinders, `Lean.unbracketedExplicitBinders, `Lean.binderIdent,
  `«term∃_,_», `«term_∧_», `«term_∨_», `«term¬_», `«term_↔_»,
  `«term_=_», `«term_≠_», `«term_<_», `«term_≤_», `«term_>_», `«term_≥_»,
  `«term_+_», `«term_-_», `«term-_», `«term_*_», `«term_/_», `«term_%_», `«term_^_»,
  `«term_∣_», `BigOperators.bigsum, `BigOperators.bigOpBinders,
  `BigOperators.bigOpBinder, `Lean.«binderTerm∈_»
]

private partial def propositionOnly : Syntax → Bool
  | .node _ kind children => allowed.contains kind && children.all propositionOnly
  | .ident .. | .atom .. => true
  | .missing => false

unsafe def main (args : List String) : IO Unit := do
  initSearchPath (← findSysroot)
  let modules :: inputs := args | throw <| IO.userError "Expected reviewed module names and proposition files."
  let names := modules.splitOn "," |>.filter (!·.isEmpty) |>.map fun moduleName =>
    (moduleName.splitOn ".").foldl Name.str .anonymous
  enableInitializersExecution
  let env ← importModules ((`Lean :: names).toArray.map ({ module := · })) {} (loadExts := true)
  for input in inputs do
    match Parser.runParserCategory env `term (← IO.FS.readFile input) with
    | .ok term =>
      unless propositionOnly term do
        throw <| IO.userError "Unsupported syntax in the exported Formalpedia proposition."
    | .error error => throw <| IO.userError error
