"""Enconvo adapter (https://enconvo.ai — macOS AI assistant).

Enconvo installs Claude Code plugins as they are, so this adapter exists for one
reason: model resolution. Enconvo honors an agent's `model:` frontmatter only when
the chat's model provider carries a matching model and otherwise runs the helper
on the chat's model — the source aliases (`opus`, `sonnet`, …) name Claude models,
so a user whose providers exclude Anthropic silently loses every tier hint.

The adapter emits self-contained per-plugin trees at `.enconvo/plugins/<plugin>/`
in the Claude Code plugin format Enconvo reads natively, with `model:` resolved to
concrete model IDs from `MODEL_ALIASES["enconvo"]` (defaults to the z.ai GLM
catalog), plus a generated `.enconvo/.claude-plugin/marketplace.json` pointing at
the trees so `enconvo plugin install .enconvo --plugin <name>` installs a resolved
copy.

Structure per plugin:

    .enconvo/plugins/<plugin>/
      .claude-plugin/plugin.json    mirrored verbatim from the source plugin
      .codex-plugin/plugin.json     mirrored when present — Enconvo reads its
                                    `interface` for the plugin's page
      agents/<agent>.md             source frontmatter with `model:` resolved;
                                    body verbatim (Enconvo reads Claude Code
                                    tool names, so nothing is rewritten)
      skills/<skill>/SKILL.md       verbatim frontmatter + body; support files
                                    (references/, assets/, …) mirrored
      commands/<command>.md         verbatim — `$ARGUMENTS` and `` !`cmd` ``
                                    lines work in Enconvo as in Claude Code
      hooks/hooks.json, *.sh        mirrored with the executable bit preserved
                                    (Enconvo installs hooks disabled until the
                                    user allows them)
      templates/ policies/ …        plugin-root support files the source plugin
                                    ships (${CLAUDE_PLUGIN_ROOT} targets), minus
                                    local caches and test fixtures
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.adapters.base import (
    AgentSource,
    CommandSource,
    EmitResult,
    HarnessAdapter,
    PluginSource,
    SkillSource,
    h1_from_body,
    yaml_scalar,
)
from tools.adapters.capabilities import resolve_model

# Every character YAML treats as structural inside a flow collection. A plain scalar
# in flow context may not contain any of them, at any position.
_YAML_FLOW_DELIMITERS = ("[", "]", "{", "}", ",")

# Fields a Claude Code plugin.json doesn't read — the marketplace entry is their
# documented home, so the adapter moves them there instead of dropping the data
# (Enconvo's validator reports them as plugin_manifest_field_unsupported).
_CLAUDE_MANIFEST_UNSUPPORTED_FIELDS = ("category", "keywords", "homepage", "license")

# Marketplace-entry fields the Enconvo validator knows (validation-codes#manifests).
# The Codex categories the source manifests use map onto OpenAI's portal set here.
_CODEX_CATEGORY_MAP = {
    "Coding": "Developer Tools",
    "coding": "Developer Tools",
    "creative": "Creativity",
    "documentation": "Education & Research",
    "marketing": "Business & Operations",
}

# interface.shortDescription limit the Enconvo validator enforces (30 characters,
# as OpenAI's portal does).
_SHORT_DESCRIPTION_LIMIT = 30

# Plugin-root entries that are the plugin's runtime surface for Enconvo. Anything
# else at the plugin root (hidden files, local caches, test fixtures) is skipped so
# a packed archive stays clean and installable.
_PLUGIN_ROOT_WHITELIST = frozenset(
    {
        "hooks",
        "templates",
        "policies",
        "stories",
        "scripts",
        "src",
        "pyproject.toml",
        "uv.lock",
        "README.md",
        ".mcp.json",
        "mcp.json",
    }
)

# Support directories never mirrored into `.enconvo/` — they hold dev-time fixtures
# and caches, not runtime surface (publishing-guidelines: keep the package clean).
_SUPPORT_DIR_EXCLUDES = frozenset({"test", "tests", ".venv", ".ruff_cache", "__pycache__"})


def _truncate_short_description(text: str) -> str:
    """Trim to the 30-char interface.shortDescription limit on a word boundary."""
    text = " ".join(text.split())
    if len(text) <= _SHORT_DESCRIPTION_LIMIT:
        return text
    cut = text[:_SHORT_DESCRIPTION_LIMIT]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:-")


def _enconvo_codex_manifest(data: dict) -> dict:
    """Normalize a `.codex-plugin/plugin.json` for Enconvo's interface schema.

    The committed Codex manifests are written for Codex's own page, whose category
    vocabulary and description lengths differ from OpenAI's portal rules Enconvo
    validates against (`plugin_category_unknown`, `plugin_short_description_too_long`).
    Enconvo reads the `interface` block for the plugin's page, so the adapter maps
    the category onto the documented enum and shortens the subtitle in the emitted
    copy — the source manifest stays untouched for clean upstream merges.
    """
    out = json.loads(json.dumps(data))  # deep copy; never mutate the loaded source
    interface = out.get("interface")
    if isinstance(interface, dict):
        category = interface.get("category")
        if category in _CODEX_CATEGORY_MAP:
            interface["category"] = _CODEX_CATEGORY_MAP[category]
        short = interface.get("shortDescription")
        if isinstance(short, str):
            interface["shortDescription"] = _truncate_short_description(short)
    return out


def _yaml_flow_scalar(value: object) -> str:
    """Render a value as one item of a YAML flow sequence (`[a, b]`).

    Flow sequences use `[`, `]`, `{`, `}` and `,` as structural delimiters, so an
    item containing any of them must be quoted even when `yaml_scalar` wouldn't
    quote it as a bare top-level scalar.
    """
    s = str(value).replace("\n", " ")
    if any(delimiter in s for delimiter in _YAML_FLOW_DELIMITERS):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return yaml_scalar(s)


def _enconvo_frontmatter(fm: dict) -> str:
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            value = ", ".join(_yaml_flow_scalar(x) for x in v)
            lines.append(f"{k}: [{value}]")
        elif isinstance(v, dict):
            # Preserve mapping-valued fields (e.g. `metadata`) as a nested YAML
            # mapping instead of stringifying the Python dict repr.
            lines.append(f"{k}:")
            for subk, subv in v.items():
                lines.append(f"  {subk}: {yaml_scalar(subv)}")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif v is None:
            continue
        else:
            lines.append(f"{k}: {yaml_scalar(v)}")
    lines.append("---")
    return "\n".join(lines)


class EnconvoAdapter(HarnessAdapter):
    """Emit self-contained Enconvo plugins with models resolved to the user's providers.

    See the module docstring for why a harness that reads the source format still
    needs an adapter: the `model:` tier hints only fire when the chat's provider
    carries a matching model ID, and the source aliases name Claude models.
    Enconvo namespaces colliding helper names itself (plugin prefix) and offers at
    most 20 helpers per chat, so bare `<plugin>`-local names are safe here.
    """

    harness_id = "enconvo"

    def emit_plugin(self, plugin: PluginSource) -> EmitResult:
        result = EmitResult()
        self._emit_manifests(plugin, result)
        for agent in plugin.agents:
            self._emit_agent(plugin, agent, result)
        for skill in plugin.skills:
            self._emit_skill(plugin, skill, result)
        for command in plugin.commands:
            self._emit_command(plugin, command, result)
        self._emit_hooks(plugin, result)
        self._emit_support_files(plugin, result)
        return result

    def emit_global(self, plugins: list[PluginSource]) -> EmitResult:
        """Emit `.enconvo/.claude-plugin/marketplace.json` for the generated trees."""
        return self._emit_marketplace(plugins)

    # ── Internals ──────────────────────────────────────────────────────────

    def _plugin_root(self, plugin: PluginSource) -> Path:
        return Path(".enconvo") / "plugins" / plugin.name

    def _emit_manifests(self, plugin: PluginSource, result: EmitResult) -> None:
        """Emit the plugin manifests, normalized for Enconvo's validators.

        `.claude-plugin/plugin.json` is what makes the folder a Claude Code-format
        plugin to Enconvo. Fields Claude Code doesn't read (`category`, `keywords`,
        `homepage`, `license`) are moved onto the marketplace entry instead — their
        documented home — so the emitted manifest validates without losing data;
        `skills` is normalized to the skills root so Enconvo's one-level
        `skills/<name>/SKILL.md` scan finds every skill.
        `.codex-plugin/plugin.json` is optional but Enconvo reads its `interface`
        listing for the plugin's page; it is normalized to the interface schema
        Enconvo validates (see `_enconvo_codex_manifest`). The source manifests stay
        untouched for clean upstream merges.
        """
        root = self._plugin_root(plugin)
        source_manifest = plugin.dir / ".claude-plugin" / "plugin.json"
        if source_manifest.is_file():
            data = json.loads(source_manifest.read_text(encoding="utf-8"))
            for field in _CLAUDE_MANIFEST_UNSUPPORTED_FIELDS:
                data.pop(field, None)
            skills = data.get("skills")
            if isinstance(skills, list):
                # A list of per-skill paths defeats Enconvo's skills/<name>/SKILL.md
                # scan (skill_file_ignored); the skills root is the documented shape.
                data["skills"] = "./skills"
            result.written.append(
                self.write(
                    root / ".claude-plugin" / "plugin.json", json.dumps(data, indent=2) + "\n"
                )
            )
        else:
            data = {"name": plugin.name}
            if plugin.description:
                data["description"] = plugin.description
            result.written.append(
                self.write(
                    root / ".claude-plugin" / "plugin.json", json.dumps(data, indent=2) + "\n"
                )
            )
        codex_manifest = plugin.dir / ".codex-plugin" / "plugin.json"
        if codex_manifest.is_file():
            codex_data = _enconvo_codex_manifest(
                json.loads(codex_manifest.read_text(encoding="utf-8"))
            )
            result.written.append(
                self.write(
                    root / ".codex-plugin" / "plugin.json",
                    json.dumps(codex_data, indent=2) + "\n",
                )
            )

    def _emit_hooks(self, plugin: PluginSource, result: EmitResult) -> None:
        """Mirror `hooks/` and any MCP config so the plugin's full surface installs.

        Enconvo installs hooks disabled until the user allows them (hooks doc), so
        mirroring is safe — but skipping them silently amputates plugins whose
        behavior lives in hooks (protect-mcp, review-agent-governance). The shell
        scripts keep their executable bit; `.mcp.json`/`mcp.json` ride along when
        present.
        """
        root = self._plugin_root(plugin)
        for rel_name in ("hooks", ".mcp.json", "mcp.json"):
            src = plugin.dir / rel_name
            if not src.exists():
                continue
            if src.is_dir():
                for file in sorted(x for x in src.rglob("*") if x.is_file()):
                    rel = file.relative_to(plugin.dir)
                    target = self.mirror_file(file, root / rel)
                    if file.suffix == ".sh" and file.stat().st_mode & 0o111:
                        target.chmod(target.stat().st_mode | 0o111)
                    result.written.append(target)
            else:
                result.written.append(self.mirror_file(src, root / rel_name))

    def _emit_support_files(self, plugin: PluginSource, result: EmitResult) -> None:
        """Mirror plugin-root support entries components reference at runtime.

        Commands, agents, hooks and skills resolve paths against the installed
        plugin root via `${CLAUDE_PLUGIN_ROOT}` (plugin-formats doc), so the support
        trees they reference must ship inside the plugin. Only whitelisted runtime
        entries are mirrored; dev-time fixtures, caches and venvs stay out.
        """
        root = self._plugin_root(plugin)
        for entry in sorted(plugin.dir.iterdir()):
            if entry.name not in _PLUGIN_ROOT_WHITELIST:
                continue
            if entry.is_dir():
                for file in sorted(x for x in entry.rglob("*") if x.is_file()):
                    rel = file.relative_to(plugin.dir)
                    if any(part in _SUPPORT_DIR_EXCLUDES for part in rel.parts):
                        continue
                    result.written.append(self.mirror_file(file, root / rel))
            else:
                result.written.append(self.mirror_file(entry, root / entry.name))

    def _emit_agent(self, plugin: PluginSource, agent: AgentSource, result: EmitResult) -> None:
        """Emit one agent with `model:` resolved; the body is kept verbatim.

        Tool names stay CamelCase (Enconvo reads the Claude Code vocabulary) and
        `color`/`permissionMode` pass through untouched.
        """
        rel = self._plugin_root(plugin) / "agents" / f"{agent.name}.md"

        model, warning = resolve_model("enconvo", agent.model)
        if warning:
            result.warnings.append(f"agent `{plugin.name}__{agent.name}`: {warning}")

        fm = dict(agent.frontmatter)
        # Normalize `tools:` to a real YAML list — the tolerant source parser keeps
        # Claude's bare `tools: Read, Grep` form as a string. Names stay CamelCase:
        # Enconvo reads the Claude Code vocabulary, so this is an identity map.
        if "tools" in agent.frontmatter:
            fm["tools"] = agent.tools
        if model and model != "inherit":
            fm["model"] = model
        else:
            # `inherit` means the chat's model applies — omit the field entirely
            # rather than emitting the literal string.
            fm.pop("model", None)

        content = _enconvo_frontmatter(fm) + "\n\n" + agent.body.rstrip() + "\n"
        result.written.append(self.write(rel, content))

    def _emit_skill(self, plugin: PluginSource, skill: SkillSource, result: EmitResult) -> None:
        """Mirror one skill verbatim (bare name — the plugin dir already scopes it)."""
        rel_dir = self._plugin_root(plugin) / "skills" / skill.name
        fm = dict(skill.frontmatter)
        fm["name"] = skill.name

        content = _enconvo_frontmatter(fm) + "\n\n" + skill.body.rstrip() + "\n"
        result.written.append(self.write(rel_dir / "SKILL.md", content))

        # Mirror every support file (references/, assets/, scripts/, …) — binary copy
        # so non-text assets don't crash the run. Skip hidden files/dirs.
        for src in sorted(skill.dir.rglob("*")):
            if not src.is_file() or src.name == "SKILL.md":
                continue
            rel = src.relative_to(skill.dir)
            if any(part.startswith(".") for part in rel.parts):
                continue
            result.written.append(self.mirror_file(src, rel_dir / rel))

    def _emit_command(
        self, plugin: PluginSource, command: CommandSource, result: EmitResult
    ) -> None:
        """Mirror one slash command verbatim.

        Enconvo installs Claude Code commands as skills named by their file path and
        runs `!`cmd`` lines and `$ARGUMENTS` itself, so the body needs no rewriting.
        """
        rel = self._plugin_root(plugin) / "commands" / f"{command.name}.md"

        fm = dict(command.frontmatter or {})
        if not fm.get("description"):
            fm["description"] = h1_from_body(command.body) or command.name.replace("-", " ").title()

        content = _enconvo_frontmatter(fm) + "\n\n" + command.body.rstrip() + "\n"
        result.written.append(self.write(rel, content))

    def _emit_marketplace(self, plugins: list[PluginSource]) -> EmitResult:
        """Emit `.enconvo/.claude-plugin/marketplace.json` for the generated trees.

        A marketplace is a folder with `.claude-plugin/marketplace.json` or
        `.agents/plugins/marketplace.json` (installing-plugins doc); the file at
        `.enconvo/` root is not one, so the CLI fails the install with
        `plugin_manifest_missing`. Sources are `./plugins/<name>` folders resolved
        from the top of the marketplace, and the fields Claude Code's plugin.json
        doesn't read (`category`, `keywords`, `homepage`, `license`) live on the
        entry — their documented home.

        `enconvo plugin install .enconvo --plugin <name>` then installs a resolved
        copy, and re-running the install after a regenerate updates it. The two
        external `git-subdir` plugins the source marketplace lists (pensyve,
                hol-guard) are carried over verbatim so the generated catalog matches the
        source one.
        """
        result = EmitResult()
        entries = []
        for plugin in plugins:
            entry: dict = {"name": plugin.name, "source": f"./plugins/{plugin.name}"}
            if plugin.description:
                entry["description"] = plugin.description
            if plugin.version and plugin.version != "0.0.0":
                entry["version"] = plugin.version
            if plugin.author.get("name"):
                entry["author"] = plugin.author
            for field in _CLAUDE_MANIFEST_UNSUPPORTED_FIELDS:
                if field in plugin.plugin_json:
                    entry[field] = plugin.plugin_json[field]
            entries.append(entry)
        entries.extend(self._external_marketplace_entries())
        data = {
            "name": "agents-enconvo",
            "owner": {
                "name": "Iteractive Development",
                "url": "https://github.com/Iteractive-Development/agents-enconvo",
            },
            "metadata": {
                "description": ("wshobson/agents plugins with model aliases resolved for Enconvo"),
                "version": "1.0.0",
            },
            "plugins": entries,
        }
        result.written.append(
            self.write(
                Path(".enconvo") / ".claude-plugin" / "marketplace.json",
                json.dumps(data, indent=2) + "\n",
            )
        )
        return result

    def _external_marketplace_entries(self) -> list[dict]:
        """Carry the source marketplace's external entries into the generated one.

        The committed `.claude-plugin/marketplace.json` lists two `git-subdir`
        plugins that have no local tree under `plugins/`; without them the generated
        catalog silently drops plugins the source marketplace offers. Only entries
        whose `source` is not a local `./plugins/<name>` folder are returned.
        """
        source = Path(".claude-plugin") / "marketplace.json"
        if not source.is_file():
            return []
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        entries: list[dict] = []
        for entry in data.get("plugins", []):
            src = entry.get("source")
            if isinstance(src, str) and src.startswith("./plugins/"):
                continue  # local tree — emitted above from the loaded plugins
            entries.append(entry)
        return entries
