import Lean

open Lean

namespace RMSourceInput

private def rejectParseErrors (messages : MessageLog) : IO Unit := do
  if messages.hasErrors then
    let descriptions ← messages.toList.mapM (·.toString)
    throw <| IO.userError (String.intercalate "\n" descriptions)

private def blockedSyntax : Array Name := #[
  `Lean.Parser.Term.sorry, `Lean.Parser.Tactic.tacticSorry, `Lean.Parser.Tactic.tacticAdmit,
  `Lean.byElab, `Lean.includeStr, `Lean.Parser.Tactic.runTac,
  `Lean.Parser.Term.set_option, `Lean.Parser.Tactic.set_option,
  `Lean.Parser.Term.attributes, `Lean.Parser.Term.quot, `Lean.Parser.Tactic.nativeDecide,
  `Lean.Parser.Tactic.bvDecide, `Lean.Parser.Tactic.bvTrace, `Lean.Parser.Tactic.bvCheck,
  `Lean.Parser.Tactic.bvDecideMacro, `Lean.Parser.Tactic.bvTraceMacro,
  `Mathlib.Meta.eval_expr, `Mathlib.Tactic.Conv.convRun_conv_
]

private partial def checkProofSyntax : Syntax → IO Unit
  | .node _ kind args => do
    if blockedSyntax.contains kind then
      throw <| IO.userError s!"This Lean submission uses forbidden syntax: {kind}."
    for arg in args do
      checkProofSyntax arg
  | .missing => throw <| IO.userError "The submitted theorem has incomplete syntax."
  | _ => pure ()

private partial def theoremName (source : Syntax) (namespaceName : Name) : IO Name := do
  if source.isOfKind ``Parser.Command.«in» && source[0].isOfKind ``Parser.Command.«open» then
    return ← theoremName source[2] namespaceName
  let noModifiers := source[0].getArgs.toList.drop 1 |>.all Syntax.isNone
  let decl := source[1]
  let (name, _) := Elab.expandDeclIdCore decl[1]
  let value := decl[3]
  unless source.isOfKind ``Parser.Command.declaration && noModifiers &&
         decl.isOfKind ``Parser.Command.theorem && !name.isAnonymous && name != `_root_ &&
         value.isOfKind ``Parser.Command.declValSimple &&
         value[2].getArgs.all Syntax.isNone && value[3].isNone do
    throw <| IO.userError "Only plain theorem NAME ... := proof declarations and supported scoping commands are allowed."
  checkProofSyntax decl
  return if (`_root_).isPrefixOf name then name.replacePrefix `_root_ .anonymous else namespaceName ++ name

private def updateScope (input : Parser.InputContext) (position : String.Pos)
    (context : Elab.Command.State) (action : Elab.Command.CommandElabM Unit) :
    IO Elab.Command.State := do
  let cmdContext : Elab.Command.Context := {
    fileName := input.fileName, fileMap := input.fileMap, cmdPos := position, snap? := none, cancelTk? := none }
  let result ← EIO.toIO' <| (action cmdContext).run context
  match result with
  | .ok (_, updated) => rejectParseErrors updated.messages; return updated
  | .error error => throw <| IO.userError (← error.toMessageData.toString)

private partial def checkCommands (input : Parser.InputContext) (context : Elab.Command.State)
    (state : Parser.ModuleParserState) (selected : String) (found : NameSet := {}) : IO Unit := do
  let scope := context.scopes.head!
  let parserContext := {env := context.env, options := scope.opts,
                        currNamespace := scope.currNamespace, openDecls := scope.openDecls}
  let position := state.pos
  let (command, state, messages) := Parser.parseCommand input parserContext state context.messages
  rejectParseErrors messages
  if command.isOfKind ``Parser.Command.eoi then
    unless found.toArray.any (fun name => name.toString == selected) do
      throw <| IO.userError "The selected fully qualified theorem is not declared in the submitted file."
    unless context.scopes.length == 1 do
      throw <| IO.userError "Close the submitted namespaces and sections before the end of the file."
  else
    -- Update parser scopes using only these built-in handlers. In particular,
    -- do not elaborate submitted proof terms or arbitrary command macros here.
    let context := { context with messages }
    let change : Option (Elab.Command.CommandElabM Unit) :=
      match command.getKind with
      | ``Parser.Command.«namespace» => some (Elab.Command.elabNamespace command)
      | ``Parser.Command.«section» => some (Elab.Command.elabSection command)
      | ``Parser.Command.«end» => some (Elab.Command.elabEnd command)
      | ``Parser.Command.«open» => some (Elab.Command.elabOpen command)
      | _ => none
    if let some change := change then
      let updated ← updateScope input position context change
      checkCommands input updated state selected found
    else if command.isOfKind ``Parser.Command.moduleDoc then
      checkCommands input context state selected found
    else if command.isOfKind ``Parser.Command.«variable» then
      checkProofSyntax command
      checkCommands input context state selected found
    else
      let name ← theoremName command scope.currNamespace
      if found.contains name then
        throw <| IO.userError s!"The submitted file declares theorem {name} more than once."
      checkCommands input context state selected (found.insert name)

private def moduleNames (text : String) : Array Name :=
  (text.splitOn ",").toArray.filterMap fun item =>
    if item.isEmpty then none
    else some ((item.splitOn ".").foldl Name.str .anonymous)

unsafe def validate (file reviewed selected : String) : IO Unit := do
  let input := Parser.mkInputContext (← IO.FS.readFile file) file
  let (header, state, messages) ← Parser.parseHeader input
  rejectParseErrors messages
  unless header[0].isNone do
    throw <| IO.userError "A submitted theorem uses the ordinary Lean prelude."
  let imports := Elab.headerToImports header
  let approved := moduleNames reviewed
  -- Read only the compile environment selected by the researcher. A submitted
  -- import never reaches the module loader until its name has been checked.
  let pinned ← importModules ((#[`Lean] ++ approved).map ({module := ·})) {}
  for item in imports do
    let isPinnedCore := #[`Init, `Lean, `Std].any (·.isPrefixOf item.module) &&
                        (pinned.getModuleIdx? item.module).isSome
    unless !item.runtimeOnly && (isPinnedCore || approved.contains item.module) do
      throw <| IO.userError s!"The submitted import {item.module} is outside the reviewed Lean environment."
  enableInitializersExecution
  let env ← importModules imports {} (loadExts := true)
  checkCommands input (Elab.Command.mkState env messages) state selected
  IO.println (Json.mkObj [("name", toJson selected)]).compress

end RMSourceInput

unsafe def main (args : List String) : IO Unit := do
  initSearchPath (← findSysroot)
  match args with
  | [file, reviewed, selected] => RMSourceInput.validate file reviewed selected
  | _ => throw <| IO.userError "Expected the Lean source path, reviewed module names, and selected theorem."
