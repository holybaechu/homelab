# Skill mechanics for Hermes

This skill vendors Matt Pocock's MIT-licensed [writing-for-agents](https://github.com/mattpocock/skills/tree/4ae2317ddb22b046e26455e4f9c9b3aaaf62212d/skills/productivity/writing-for-agents)
at revision `4ae2317ddb22b046e26455e4f9c9b3aaaf62212d`. The main guide is unchanged
except for its discovery description. This reference adapts the upstream
`SKILL-MECHANICS.md` invocation and router advice to Hermes; the writing principles
in [SKILL.md](SKILL.md) remain the universal reference. See [LICENSE](LICENSE).

## Invocation

Use a short, model-facing `description` carrying the trigger branches when the
agent should discover the skill. Hermes lists available skills and loads their
bodies on demand with `skill_view`; users can also invoke installed skills by
slash command. Keep shared reference behind a pointer when only one branch needs
it, and choose a separate skill only when independent discovery earns the
always-loaded description.

The upstream `disable-model-invocation: true` advice describes Claude Code, not a
portable Hermes contract. Keep Hermes skill frontmatter consistent with its
installed skill tools. For creating or editing skills, also load
`hermes-agent-skill-authoring` for the local format and lifecycle rules.

## Routing

Place automatic routing in the effective managed instruction source, naming the
skill and the distinct branches that require it. For a human-only index, use a
plain reference document; follow Hermes's authoring guidance rather than copying
the upstream user-only router-skill mechanism.

## Learning-driven instruction updates

When verified learning warrants a durable instruction change, use the main guide
to sharpen the trigger, process, and checkable completion criterion in the owning
instructions. Merge corrections into the existing source and retire superseded
rules. Keep knowledge in the appropriate memory, skill, wiki, or project store;
this guide changes writing quality, not write permissions or capture policy.
