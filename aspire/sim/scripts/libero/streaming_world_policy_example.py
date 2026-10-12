# SPDX-License-Identifier: Apache-2.0
"""Saved-policy integration example, not a solved manipulation task.

Use only with the explicit sam2-streaming-world-v1 replay profile. The replay
runner initializes the target from this episode's public SAM3 observation.
All functions below are injected into the policy namespace by that runner.
"""


def checkpoint(snapshot):
    target = snapshot["observed"].get("bowl")
    if target is None or target["status"] != "known":
        print("Bowl tracking is missing or stale; stop further motion.", flush=True)
        return False
    return True


set_world_checkpoint(checkpoint)
print("Initial public world:", world_snapshot(), flush=True)
# The checkpoint runs DURING this blocking call, including home motion.
request_world_verification()
goto_home_joint_position()
print("World after motion:", world_snapshot(), flush=True)

# A task policy can install its own verifier:
# def verify(world, at):
#     world.verify_relation("task_relation", ["bowl", "robot"],
#                           authored_predicate, at=at)
# set_world_relation_verifier(verify)
# request_world_verification()
# Contact/grasp/placement predicates must come from admissible public evidence.
