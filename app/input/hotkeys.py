"""Global hotkeys. Callbacks run on the keyboard library's thread; keep them short."""

from __future__ import annotations

from collections.abc import Callable

DEFAULT_HOTKEYS = {
    "ask": "ctrl+alt+space",     # "what is on screen and what was just said?"
    "repeat": "ctrl+alt+r",      # repeat the last answer
    "stop": "ctrl+alt+x",        # stop speaking
    "status": "ctrl+alt+s",      # what is captured right now
    "pause": "ctrl+alt+p",       # pause / resume capture
}


def register(actions: dict[str, Callable[[], None]],
             hotkeys: dict[str, str] = DEFAULT_HOTKEYS) -> Callable[[], None]:
    """Register hotkeys for the given actions. Returns a function that removes them."""
    import keyboard

    handles = [keyboard.add_hotkey(hotkeys[name], fn, suppress=False)
               for name, fn in actions.items() if name in hotkeys]

    def unregister() -> None:
        for h in handles:
            keyboard.remove_hotkey(h)

    return unregister


def describe(hotkeys: dict[str, str] = DEFAULT_HOTKEYS) -> str:
    return ", ".join(f"{keys.replace('+', ' ')} to {name}" for name, keys in hotkeys.items())
