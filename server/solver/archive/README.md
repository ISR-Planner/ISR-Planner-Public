# server/solver/archive — Legacy Solver Versions

These files have been archived as part of the repository cleanup. They are **not used** in the production solver pipeline and are preserved for historical reference only.

| File | Was | Reason archived |
|------|-----|-----------------|
| `post_optimizer_v1.py` | First version of the post-optimization passes | Zero active imports; superseded by `server/solver/post_optimizer.py` |
| `target_allocator_v1.py` | First version of the target allocation strategy engine | Zero active imports; superseded by `server/solver/target_allocator.py` |

The active solver files are `server/solver/solver_bridge.py`, `server/solver/post_optimizer.py`, and `server/solver/target_allocator.py`.
