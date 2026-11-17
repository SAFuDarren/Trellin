import Lean

open Lean

namespace RMProofData

def constants (value : Expr) : Array Name :=
  runST fun world => do
    let names ← ST.mkRef (σ := world) (m := ST world) value.getUsedConstants
    value.forEach (ω := world) (m := ST world) fun
      | .proj name _ _ => names.modify (·.push name)
      | _ => pure ()
    names.get

def kernelChecks (env : Environment) (type value : Expr) : Bool :=
  match Kernel.check env {} value with
  | .ok inferred => Kernel.isDefEqGuarded env {} inferred type
  | .error _ => false

private def row (tag : String) (args : Array Json := #[]) : Json :=
  .arr (#[toJson tag] ++ args)

private partial def nameComponents : Name → List Json → List Json
  | .anonymous, parts => parts
  | .str parent value, parts => nameComponents parent (toJson value :: parts)
  | .num parent value, parts => nameComponents parent (toJson value :: parts)

private def encodeName (name : Name) : Json := .arr (nameComponents name []).toArray

private def decodeName (data : Json) : Except String Name := do
  (← data.getArr?).foldlM (init := .anonymous) fun parent part =>
    match part with
    | .str value => pure (.str parent value)
    | value => do pure (.num parent (← value.getNat?))

private partial def encodeLevel (parameters : List Name) (level : Level)
    (depth : Nat := 0) : Except String Json := do
  if depth > 120 then throw "The universe expression exceeds the portable depth budget."
  let encode := fun argument => encodeLevel parameters argument (depth + 1)
  match level with
  | .zero => pure (row "zero")
  | .succ level => pure (row "succ" #[← encode level])
  | .max left right => pure (row "max" #[← encode left, ← encode right])
  | .imax left right => pure (row "imax" #[← encode left, ← encode right])
  | .param name =>
    let some index := parameters.idxOf? name | throw "Unbound universe in portable proof."
    pure (row "parameter" #[toJson index])
  | .mvar _ => throw "Unresolved universe in portable proof."

private partial def decodeLevel (parameters : Array Name) (data : Json)
    (depth : Nat := 0) : Except String Level := do
  if depth > 120 then throw "The universe expression exceeds the portable depth budget."
  let decode := fun argument => decodeLevel parameters argument (depth + 1)
  match (← data.getArr?).toList with
  | [.str "zero"] => pure .zero
  | [.str "succ", level] => pure (.succ (← decode level))
  | [.str "max", left, right] => pure (.max (← decode left) (← decode right))
  | [.str "imax", left, right] => pure (.imax (← decode left) (← decode right))
  | [.str "parameter", index] =>
    match parameters[← index.getNat?]? with
    | some name => pure (.param name)
    | none => throw "Unknown universe parameter in portable proof."
  | _ => throw "Invalid universe in portable proof."

private def encodeBinder : BinderInfo → Nat
  | .default => 0
  | .implicit => 1
  | .strictImplicit => 2
  | .instImplicit => 3

private def decodeBinder (data : Json) : Except String BinderInfo := do
  match ← data.getNat? with
  | 0 => pure .default
  | 1 => pure .implicit
  | 2 => pure .strictImplicit
  | 3 => pure .instImplicit
  | _ => throw "Invalid Lean binder in portable proof."

-- De Bruijn indices preserve binding independent of the source's local names.
private partial def encodeExpr (parameters : List Name) (value : Expr)
    (depth : Nat := 0) : Except String Json := do
  if depth > 240 then throw "The kernel expression exceeds the portable depth budget."
  let encode := fun argument => encodeExpr parameters argument (depth + 1)
  match value with
  | .bvar index => pure (row "var" #[toJson index])
  | .fvar _ | .mvar _ => throw "The portable proof must be closed."
  | .sort level => pure (row "sort" #[← encodeLevel parameters level])
  | .const name levels =>
    pure (row "const" #[encodeName name, toJson (← levels.mapM (encodeLevel parameters))])
  | .app fn arg => pure (row "app" #[← encode fn, ← encode arg])
  | .lam _ domain body info | .forallE _ domain body info =>
    pure (row (if value.isLambda then "lambda" else "forall") #[toJson (encodeBinder info), ← encode domain, ← encode body])
  | .letE _ type value body _ => pure (row "let" #[← encode type, ← encode value, ← encode body])
  | .lit (.natVal value) => pure (row "nat" #[toJson value])
  | .lit (.strVal value) => pure (row "string" #[toJson value])
  | .mdata _ body => encode body
  | .proj name index expr => pure (row "project" #[encodeName name, toJson index, ← encode expr])

partial def decodeExpr (data : Json) (depth : Nat := 0) : Except String Expr := do
  if depth > 240 then throw "The kernel expression exceeds the portable depth budget."
  let decode := fun argument => decodeExpr argument (depth + 1)
  match (← data.getArr?).toList with
  | [.str "var", index] => pure (.bvar (← index.getNat?))
  | [.str "sort", level] => pure (.sort (← decodeLevel #[] level))
  | [.str "const", name, .arr levels] =>
    pure (.const (← decodeName name) (← levels.toList.mapM (decodeLevel #[])))
  | [.str "app", fn, arg] => pure (.app (← decode fn) (← decode arg))
  | [.str "lambda", info, domain, body] =>
    pure (.lam `p (← decode domain) (← decode body) (← decodeBinder info))
  | [.str "forall", info, domain, body] =>
    pure (.forallE `p (← decode domain) (← decode body) (← decodeBinder info))
  | [.str "let", type, value, body] =>
    pure (.letE `v (← decode type) (← decode value) (← decode body) false)
  | [.str "nat", value] => pure (.lit (.natVal (← value.getNat?)))
  | [.str "string", .str value] => pure (.lit (.strVal value))
  | [.str "project", name, index, expr] =>
    pure (.proj (← decodeName name) (← index.getNat?) (← decode expr))
  | _ => throw "Invalid kernel expression in portable proof."

private def semanticValue (env : Environment) (info : ConstantInfo) : Option Expr :=
  match info with
  | .defnInfo decl =>
    let levels := decl.levelParams.map fun _ => Level.succ .zero
    let type := decl.type.instantiateLevelParams decl.levelParams levels
    match Kernel.check env {} type with
    | .ok sort => if Kernel.isDefEqGuarded env {} sort (.sort .zero) then none else some decl.value
    | .error _ => some decl.value
  | .opaqueInfo decl =>
    let levels := decl.levelParams.map fun _ => Level.succ .zero
    let type := decl.type.instantiateLevelParams decl.levelParams levels
    match Kernel.check env {} type with
    | .ok sort => if Kernel.isDefEqGuarded env {} sort (.sort .zero) then none else some decl.value
    | .error _ => some decl.value
  | _ => none

private def generated (info : ConstantInfo) : Array Name :=
  match info with
  | .inductInfo data => data.all.toArray ++ data.ctors.toArray
  | .ctorInfo data => #[data.induct]
  | .recInfo data => data.all.toArray ++ data.rules.toArray.flatMap (fun rule => #[rule.ctor] ++ constants rule.rhs)
  | _ => #[]

partial def closure (env : Environment) (proofValues : Bool) (pending : List Name)
    (seen : NameSet := {}) : Except String NameSet := do
  match pending with
  | [] => pure seen
  | name :: rest =>
    if seen.contains name then closure env proofValues rest seen
    else
      let some info := env.checked.get.find? name | throw s!"Lean cannot resolve required declaration {name}."
      let value := if proofValues then info.value? true else semanticValue env info
      let next := constants info.type ++ ((value.map constants).getD #[]) ++ generated info
      closure env proofValues (next.toList ++ rest) (seen.insert name)

private def signature (env : Environment) (name : Name) : Except String Json := do
  let some info := env.checked.get.find? name | throw s!"Lean cannot resolve required declaration {name}."
  let encode := encodeExpr info.levelParams
  let metadata ← match info with
    | .axiomInfo _ => pure (row "axiom")
    | .defnInfo data =>
      let hint := match data.hints with
        | .opaque => "opaque"
        | .abbrev => "abbrev"
        | .regular _ => "regular"
      pure (row "definition" #[toJson hint])
    | .thmInfo _ => pure (row "theorem")
    | .opaqueInfo _ => pure (row "opaque")
    | .quotInfo data =>
      let kind := match data.kind with
        | .type => "type"
        | .ctor => "constructor"
        | .lift => "lift"
        | .ind => "induction"
      pure (row "quotient" #[toJson kind])
    | .inductInfo data => pure (row "inductive" #[toJson data.numParams, toJson data.numIndices,
      toJson (data.all.map encodeName), toJson (data.ctors.map encodeName), toJson data.numNested,
      toJson data.isRec, toJson data.isReflexive])
    | .ctorInfo data => pure (row "constructor" #[encodeName data.induct, toJson data.cidx,
      toJson data.numParams, toJson data.numFields])
    | .recInfo data =>
      let rules ← data.rules.mapM fun rule => do
        pure (Json.arr #[encodeName rule.ctor, toJson rule.nfields, ← encode rule.rhs])
      pure (row "recursor" #[toJson (data.all.map encodeName), toJson data.numParams, toJson data.numIndices,
        toJson data.numMotives, toJson data.numMinors, toJson data.k, toJson rules])
  let body ← (semanticValue env info).mapM encode
  return Json.arr #[encodeName name, toJson info.levelParams.length, ← encode info.type,
    body.getD .null, toJson info.isUnsafe, toJson info.isPartial, metadata]

def declarationChanges (env : Environment) (report : Array Json) : Except String (Array Json) := do
  let mut changes := #[]
  for saved in report do
    let fields ← saved.getArr?
    let some encodedName := fields[0]? | throw "A saved declaration identity is missing."
    let name ← decodeName encodedName
    if (signature env name).toOption != some saved then
      changes := changes.push (Json.mkObj [
        ("declaration", toJson name.toString),
        ("reason", toJson (if (env.checked.get.find? name).isSome then "meaning_changed" else "missing"))])
  return changes

def fingerprintScope (env : Environment) (types : Array Expr) : Except String (Array Json) := do
  let names ← closure env false (types.flatMap constants).toList
  let ordered := names.toArray.qsort (fun left right => left.toString < right.toString)
  ordered.mapM (signature env)

private abbrev Expand := StateT Nat (Except String)

private partial def unfoldLocals (env : Environment) (isLocal : Name → Bool)
    (term : Expr) : Expand Expr := do
  let remaining ← get
  if remaining == 0 then throw "The proof term exceeds the portable expansion budget."
  set (remaining - 1)
  let unfold := unfoldLocals env isLocal
  match term with
  | .const name levels =>
    if isLocal name then
      let some info := env.checked.get.find? name | throw "A source-local declaration is missing."
      let some value := info.value? true | throw "A source-local declaration has no portable kernel body."
      unfold (value.instantiateLevelParams info.levelParams levels)
    else pure term
  | .app fn arg => pure (.app (← unfold fn) (← unfold arg))
  | .lam name domain body kind => pure (.lam name (← unfold domain) (← unfold body) kind)
  | .forallE name domain body kind => pure (.forallE name (← unfold domain) (← unfold body) kind)
  | .letE name type value body nonDep => pure (.letE name (← unfold type) (← unfold value) (← unfold body) nonDep)
  | .mdata _ body => unfold body
  | .proj name index value => pure (.proj name index (← unfold value))
  | _ => pure term

private structure Requirement where
  id : String
  name : Name
  type : Expr
  deriving Inhabited

private def prerequisiteOrder (env : Environment) (approvals : Array (String × Name))
    (roots : Array Expr) : Except String (Array Requirement) := do
  let mut visible : NameSet := (roots.flatMap constants).foldl (·.insert ·) {}
  for _ in approvals do
    for (_, name) in approvals do
      if visible.contains name then
        let some info := env.checked.get.find? name | throw "An approved portable dependency is missing."
        if !info.levelParams.isEmpty then throw "A portable theorem dependency has unresolved declaration universes."
        visible := (constants info.type).foldl (·.insert ·) visible
  let mut remaining := approvals.filter (visible.contains ·.2)
  let mut ordered : Array Requirement := #[]
  for _ in remaining do
    let pending : NameSet := remaining.foldl (fun found (_, name) => found.insert name) {}
    let some (id, name) := remaining.find? (fun (_, name) =>
      ((env.checked.get.find? name).map (constants ·.type)).getD #[] |>.all (!pending.contains ·))
      | throw "Portable theorem dependencies form an unresolved cycle."
    let some info := env.checked.get.find? name | throw "An approved portable dependency is missing."
    ordered := ordered.push { id, name, type := info.type }
    remaining := remaining.filter (·.2 != name)
  return ordered

def exportProof (env : Environment) (type proof : Expr) (isLocal : Name → Bool)
    (approvals : Array (String × Name)) : Except String Json := do
  let (type, remaining) ← (unfoldLocals env isLocal type).run 150000
  let (proof, _) ← (unfoldLocals env isLocal proof).run remaining
  let prerequisites ← prerequisiteOrder env approvals #[type, proof]
  let variables := prerequisites.mapIdx (fun index _ =>
    Expr.fvar { name := Name.num `RMProofData.prerequisite index })
  let replace := fun expr => expr.replace fun term =>
    match term with
    | .const name [] => (prerequisites.findIdx? (·.name == name)).map (variables[·]!)
    | _ => none
  let mut type := replace type
  let mut proof := replace proof
  for (entry, var) in (prerequisites.zip variables).reverse do
    let domain := replace entry.type
    type := .forallE `dependency domain (type.abstract #[var]) .default
    proof := .lam `dependency domain (proof.abstract #[var]) .default
  unless kernelChecks env type proof do
    throw "Lean could not recheck the portable form of the original proof."
  let declarations ← fingerprintScope env #[type, proof]
  let dependencies := prerequisites.map fun entry => Json.mkObj [
    ("version_id", toJson entry.id), ("declaration", toJson entry.name.toString)]
  let artifact := Json.mkObj [
    ("format_version", toJson (1 : Nat)), ("type", ← encodeExpr [] type),
    ("proof", ← encodeExpr [] proof), ("requirements", toJson dependencies),
    ("declarations", toJson declarations)]
  if artifact.compress.utf8ByteSize > 10 * 1024 * 1024 then
    throw "The audited proof exceeds the portable artifact size budget."
  return artifact

end RMProofData
