# agents-enconvo

A synced copy of [wshobson/agents](https://github.com/wshobson/agents) (MIT) with an
Enconvo harness adapter. Upstream is pulled weekly; this repo only adds:

- `tools/adapters/enconvo.py` — emits `.enconvo/` (gitignored): one resolved copy
  of each upstream plugin plus `marketplace.json`.
- the `enconvo` entry in `tools/adapters/capabilities.py` — capability matrix and
  the model alias map.

## Model resolution

Agent `model:` aliases are rewritten to the z.ai GLM catalog in
`MODEL_ALIASES["enconvo"]`:

| Upstream alias | Resolved model |
| --- | --- |
| `fable`, `opus` | `glm-5.3` |
| `sonnet`, `haiku` | `glm-5.3-flash` |
| `inherit` | omitted — the chat's model applies |

Edit that one dict to retarget the catalog. Skills and commands carry no model
fields upstream and pass through unchanged.

## Sync and install loop

```bash
git pull upstream main                          # bring in upstream
git push origin main                            # keep this repo current
make generate HARNESS=enconvo                   # regenerate .enconvo/
for d in .enconvo/plugins/*/; do                # install: sync each plugin
  rsync -a --delete "$d" "$HOME/.config/enconvo/extension/$(basename "$d")"

done
```

The npm `@enconvo/api` CLI (0.1.174) predates marketplace and folder installs,
so plugins are delivered by syncing into Enconvo's extension directory — the same
location its dev builds use. **Restart Enconvo** to load catalog changes. Scope
`--delete` per plugin folder only; never at the extension directory root.

A scheduled Enconvo job runs this loop weekly and reports what changed.

## License

Upstream content is MIT © Seth Hobson and contributors; the adapter files are
added under the same license.
