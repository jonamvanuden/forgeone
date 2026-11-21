# registry

Keeps track of every agent version and which one is the **champion**.

**Planned**
- Agent version = hash of its spec; linked to the build run that produced it
- Champion per agent, changed only by `forgeone.evals.gate.decide`
- Publish a promoted version (later: to its own repository as a pull request)
