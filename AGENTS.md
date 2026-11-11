# InvaPlan development instructions

- Deliver every new user-facing feature in Simplified Chinese and English in the same change.
- Keep interface labels, messages, help text, map controls and accessibility names in the shared locale dictionaries in `src/i18n.ts`. Keep both dictionaries complete for each message key. Add both names for new app-owned display metadata when applicable.
- Verify the feature in both languages, including its empty states, errors and confirmation steps as applicable. The language switch should preserve the active plan, selection and map position.
- Keep identifiers and machine-readable export fields stable. Preserve user-entered notes verbatim.
