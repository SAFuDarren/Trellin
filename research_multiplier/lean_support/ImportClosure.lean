import Lean

open Lean

-- Read compiled import metadata without loading project extensions.
private partial def collect (pending : List Name) (seen : NameSet := {})
    (paths : Array String := #[]) : IO (Array String) := do
  match pending with
  | [] => return paths
  | name :: rest =>
    if seen.contains name then
      collect rest seen paths
    else
      let path ← findOLean name
      let (metadata, _) ← readModuleData path
      collect (metadata.imports.toList.map (·.module) ++ rest) (seen.insert name) (paths.push path.toString)

def main (args : List String) : IO Unit := do
  let modules := (args.takeWhile (· != "roots:")).map fun name => (name.splitOn ".").foldl Name.str .anonymous
  let roots := ((args.dropWhile (· != "roots:")).drop 1).map System.FilePath.mk
  initSearchPath (← findSysroot) roots
  IO.println (toJson (← collect (`Lean :: modules))).compress
