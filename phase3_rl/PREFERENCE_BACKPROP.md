# Preference Backpropagation Credit

This RL path uses final-video quality as an indirect meta-signal, not as a
direct scalar policy reward.

For each training batch, valid exports with the same fixture path are grouped.
Pairwise preferences inside that group update a small request-conditioned,
artifact-grounded Bradley-Terry allocator. The current PPO batch always uses
the allocator state from before the current judge results were observed:

```text
same-task final-video preference
  -> pairwise allocator update
  -> frozen allocator for the next batch
  -> zero-sum segment reward redistribution
```

Consecutive tool events in the same editing stage form one segment. Segment
reward is placed only at the segment-ending assistant span. Critic-based
PPO/GAE then propagates return through the trajectory. The allocator writes
feature-level explanations to `preference_backprop`; its allocation sums to
zero per trajectory, so the scalar rule return remains unchanged.

Only successful exports can update the allocator. Judge evidence is restricted
to the user request and ordered samples from the final video; tool traces and
blueprints are excluded from the product judge.
