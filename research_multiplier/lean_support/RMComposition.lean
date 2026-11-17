import NativeReplay

open Lean Elab Tactic

elab "rm_native " archive:str origin:num destination:num : tactic => do
  let goal ← getMainGoal
  let value ← goal.withContext <|
    RMNativeReplay.composeProof (← goal.getType) archive.getString origin.getNat destination.getNat
  goal.assign value
  replaceMainGoal []
