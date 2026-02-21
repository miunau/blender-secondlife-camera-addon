# Changelog

## 1.0.4

- Fix ALT+Click conflict with edge loop / edge ring selection in edit mode
- Keymap registrations changed from PRESS to CLICK_DRAG + CLICK, allowing Blender to disambiguate between click (loop select) and drag (camera control)
- Initial raycast on drag now uses the original press position for accurate focus targeting

## 1.0.3

- Supports "Lock Camera to View" (Numpad `0` and then toggling camera lock to sync camera position with view)
- Small refactor to internals
- Fix view jerking while moving mouse during initial transition

## 1.0.2

- Add changelog
- Changed `__name__` to `__package__` in `__init__.py`
