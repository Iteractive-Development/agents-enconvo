# agents-enconvo

A synced copy of [wshobson/agents](https://github.com/wshobson/agents) (MIT) with an
Enconvo harness adapter. Upstream is pulled weekly; this repo only adds:

- `tools/adapters/enconvo.py` — emits `.enconvo/` (gitignored): one resolved copy
  of each upstream plugin plus `.claude-plugin/marketplace.json`.
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
make install-enconvo ENCONVO_PLUGIN=<name>      # generate + install one plugin
```

`make install-enconvo` generates `.enconvo/` (gitignored) and then runs the Enconvo
CLI bundled with the app — `node $ENCONVO_CLI plugin install .enconvo --plugin
<name>` — because the npm `@enconvo/api` CLI (0.1.174) predates the plugin
subcommands, and a marketplace source requires `--plugin` (without it the CLI just
lists the catalog; a marketplace install needs Enconvo 2.5.6+ running). To install
everything at once, `make generate HARNESS=enconvo` then rsync each tree into
`$HOME/.config/enconvo/extension/<name>` (scope `--delete` per plugin folder only,
never at the extension directory root) and **restart Enconvo** to load the catalog.

A scheduled Enconvo job runs this loop weekly and reports what changed.

## License

Upstream content is MIT © Seth Hobson and contributors; the adapter files are
added under the same license.
