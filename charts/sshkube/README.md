# sshkube
Enable access to your kubernetes cluster over ssh:
- ssh public key used to authenticate users to the ssh server
- kubeconfig is available on the ssh server
- all kubectl commands run on the host but routed through an SSH facilitated SOCKS5 HTTPS proxy

## chart
This chart deploys the ssh server which permits all github users listed in Values.yaml `github-users` to access the ssh server through authorized_keys based on the user's github registered public keys.

The service also creates a kubernetes service account, namespace, and kubeconfig for the user. The user can then use the sshkube python library to easily configure access to the cluster.
