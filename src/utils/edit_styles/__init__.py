"""Edit styles: per-video layouts rotated across a batch, plus batch-wide modifiers.

- ``spec``: catalogue of types and their settings fields.
- ``store``: saved records (variants) with params and an enabled flag.
- ``graph``: filter fragments emitted for the GPU or the CPU overlay chain.
- ``assets``: procedural PNG/MOV graphics, cached by params.
- ``chapters``: chapter/quote timing from a ``.chapters.txt`` file or the SRT.
- ``runtime``: what one render needs from its layout + modifiers.
"""
