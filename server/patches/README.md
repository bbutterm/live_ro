# Патчи исходников upstream

Изменения исходников rAthena/OpenKore храним здесь, а не как незакоммиченные
правки внутри submodule (их родительский commit не сохраняет).

- `rathena/NNNN-описание.patch` — для `upstream/rathena`;
- `openkore/NNNN-описание.patch` — для `upstream/openkore`.

`scripts/lab deploy` применяет их по порядку имён (`git apply`) к чистой копии
submodule в новом release-каталоге и прерывается, если патч не накладывается.
Как сделать патч — см. `docs/SUBMODULES.md`.
