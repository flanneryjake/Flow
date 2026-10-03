---
name: self-improve
triggers: \b(train\w*|training data|rebuild\w*|modelfile|house rules?|persona|fine-?tun\w*|lesson\w*|suggest|proposal)\b
summary: Getting better: Jarvis and TARS may suggest new training rows, a model rebuild or a house-rules change on a "Proposal:" card (lessons in jarvis/tars/training/lessons); training data needs no approval, live rebuilds wait for Jake's typed line.
---
- Training data: write it in Git, run check_training.py, add it with training_intake.py (logged, undoable). No secrets or patient details.
- Rebuild a model: back it up with ollama cp first, ollama create from the new Modelfile, never restart Ollama on the rig. Needs Jake's line.
- House rules: edit a card in jarvis/knowledge/cards, then knowledge.py modelfile --base jarvis, back up, ollama create. Needs Jake's line.
- Suggest these on a "Proposal:" card with a real example, the steps, the check and the undo. Never approve your own card.
