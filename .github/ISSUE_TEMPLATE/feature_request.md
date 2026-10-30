---
name: Feature request
about: Suggest a new feature or improvement for K.H.A.V.I.S.
title: "[feature] "
labels: ["enhancement", "triage"]
assignees: []
---

## Feature summary

A clear and concise description of the feature you are proposing.

## Motivation / use case

Describe the problem this feature would solve. Why is it important? What
workflow does it unlock? Who benefits (end-users, integrators, plugin
authors)?

## Proposed solution

Describe how you would like this to work — API shape, config schema,
command-line interface, etc.

```yaml
# Example new config knob
routing:
  strategy: cost-aware
  fallback_order: [fast, cheap, premium]
```

## Alternatives considered

What other approaches have you considered? Why is the proposed solution
better? Are there trade-offs we should be aware of?

## Additional context

Links to issues, PRs, external docs, or prior art in other projects that
informed this request.

## Willingness to contribute

- [ ] I am willing to submit a PR implementing this feature
- [ ] I am willing to help test / review a PR
- [ ] I would prefer maintainers to implement this