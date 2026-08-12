Integration instructions — JONY UI prototype
===========================================

What the panel looks like
-------------------------
`johnny/panel.py` follows the design spec:
  - Palette from the spec (#0D0D0E background, periwinkle cards, lime accents).
  - Top status header with a `JONY` logo.
  - Central periwinkle control card with a large `Listen` toggle.
  - Existing settings widgets (voice, recognition, model) below the main card.
  - A **Тулкиты** section with a button per external tool.
  - Existing callbacks and external hooks unchanged (`on_save`, `on_toggle_pause`, …).

Files to review
----------------
- `johnny/panel.py` — `_build_ui`, palette constants, `_tools_section`, `_refresh_status`, `_update_pause_button`.
- `johnny/panel_tools.py` — toolkit logic, kept free of Tk.

How to enable the new UI at runtime
----------------------------------
Nothing to enable — the layout above is what `johnny/panel.py` already draws. Open
it the usual way: tray → «Настройки» (or `python main.py --no-env-prompt`).

Assets
------
Both PNGs are **absent** from this directory, despite the earlier claim that they
were generated:

- `logo_raster.png` — header shows the text `JONY` instead
- `pixel_wave_raster.png` — control card shows a text placeholder instead

Nothing is broken by this, and the panel does not need them to work. Drop the
files in if you want the raster versions; `tk.PhotoImage` reads PNG and GIF only,
so keep those formats.

Font `JetBrains Mono` may be missing on a given machine; Tk falls back to a system
font on its own.

Toolkits section
----------------
Added after the prototype: a **Тулкиты** section with a card per external tool
(reverse image search, face search, profiles by nickname, translation). Each card
runs the *same registry action the voice command runs* — see
[docs/connectors.md](../../connectors.md#кнопки-в-панели) for why that matters and
how the readiness line and the background queue work.

Logic lives in `johnny/panel_tools.py`, deliberately free of Tk so it can be
tested; `tests/test_panel_tools.py` covers it.

Prototype gaps closed
---------------------
- The three quick-action pills (История / Лог / Автозапуск) had **no `command` at
  all** — they looked live and did nothing. A button that silently does nothing is
  worse than a missing one: people press it and conclude the app is broken.
- Status badges were hardcoded strings claiming readiness regardless of the actual
  settings.

How to revert
-------------
This project is not a git repository, so there is nothing to check out. Keep a
copy of `johnny/panel.py` before large UI edits if you want a way back.

Next steps (optional)
---------------------
- Replace placeholder pixel animations with proper sprite frames.
- Tidy up spacing and micro-interactions after testing on the target display.
- Note that headless `SettingsPanel` tests are not worth mocking Tk for: the logic
  worth asserting is already in `panel_tools.py`. Real Tk smoke runs caught a bug
  mocks would have hidden (`self.after` called from a worker thread).
