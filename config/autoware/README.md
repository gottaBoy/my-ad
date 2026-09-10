# Autoware Configuration Boundary

The selected Autoware image owns the exact launch package and vehicle/map
configuration. Do not embed a guessed launch command in Compose.

Set `AUTOWARE_COMMAND` in `.env` to the command validated for the selected
image. The wrapper executes that command as PID 1 and propagates its exit code
and termination signal.

The command must:

1. source the image's ROS and Autoware setup files;
2. enable simulation time;
3. load the selected map and vehicle model;
4. select the AWSIM sensor kit or the Scenario Simulator adapter;
5. remain in the foreground for the lifetime of Autoware.

Set `AUTOWARE_HEALTHCHECK_COMMAND` separately. It must be a positive readiness
probe, such as checking a required ROS node, topic, lifecycle state, or local
service endpoint. A process-only check such as `pgrep` is insufficient for an
integration `PASS`.
