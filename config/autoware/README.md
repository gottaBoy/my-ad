# Autoware Configuration Boundary

The selected Autoware image owns the exact launch package and vehicle/map
configuration. Do not embed a guessed launch command in Compose.

Set `AUTOWARE_COMMAND` in `.env` to the command validated for the selected
image. The default entrypoint only writes a readiness marker and keeps the
container alive so image and GPU checks can run before integration.

The command must:

1. source the image's ROS and Autoware setup files;
2. enable simulation time;
3. load the selected map and vehicle model;
4. select the AWSIM sensor kit or the Scenario Simulator adapter;
5. write `/tmp/autoware.ready` after the process is healthy.
