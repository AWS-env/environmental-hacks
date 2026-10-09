# Per-Issue Verification Plan

Copy this section into a comment on the existing detector issue. Fill it before
implementation and link test references from the PR. No separate issue is needed
for each case. See [the contract](DETECTOR_CONTRACT.md) for field semantics.

```markdown
## Verification plan

Check ID:
Supported language/configuration/provider formats:
Required evidence and scope:
Detection rule and legitimate exceptions:
Semantic identity used for fingerprints:
Context settings affecting evaluation:
Unsupported inputs and limitations:

| Case ID | Supplied input / condition | Expected findings, evidence and status | Fixture / test |
| --- | --- | --- | --- |
| <KEY>-01 | Known positive | Finding with exact evidence; completed scope | Pending |
| <KEY>-02 | Similar negative | No finding; completed scope | Pending |
| <KEY>-03 | Legitimate exception | Rule-specific expected outcome | Pending |
| <KEY>-04 | Required evidence missing | Unavailable/partial, with reason | Pending |
| <KEY>-05 | Malformed/unsupported input | Explicit error/unavailable/partial; no clean claim | Pending |
| <KEY>-06 | Relevant threshold/identity boundary | Define exact expected outcome | Pending |

Validation command:
Dependencies still missing:
Reviewer challenge case:

- [ ] Input/result pair passes shared contract and evidence validation.
- [ ] Tests assert intended behavior, evidence and coverage.
- [ ] Cases catch always-empty and always-flag implementations.
- [ ] Unsupported impact values remain absent.
- [ ] Reviewer reproduced relevant tests on the latest PR revision.
```

Choose additional cases according to risk. For telemetry checks, consider sample
size and time windows. For static checks, consider comments, strings, scope and
configuration precedence. Use fixtures to develop offline; mark them as synthetic
and do not describe them as production verification.
