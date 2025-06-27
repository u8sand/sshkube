# sshkube

Configure access to a kubernetes cluster over ssh:
- ssh public key used to authenticate users to the ssh server
- kubeconfig is available on the ssh server
- all kubectl commands run on the host but routed through an SSH facilitated SOCKS5 HTTPS proxy

## SSH Server
- An ssh server enables login by users with public keys fetched from github
- The server contains a .kube/config file usable for accessing the kubernetes cluster
- The server has tcp forwarding enabled

## SSHKube Client Library
- A python CLI
- SSHs to the ssh server and obtains the .kube/config file
- Runs a SOCKS5 proxy server as a daemon
- prepares environment variables for kubectl to route requests through the SOCKS5 proxy server

## Install on Cluster
```bash
# create demo cluster
k3d cluster create -a1 -p "80:80@loadbalancer" -p "443:443@loadbalancer"

DOMAIN=sshkube.localhost.u8sand.net

# install the sshkube chart
#   users specified line-by-line in githubUsers will be able to authenticate against the cluster
#   storage is used for ssh host keys persistence
#   ingress is used to forward ssl connections to the given domain to the ssh server
helm install --create-namespace -n sshkube sshkube ./charts/sshkube/ -f - << EOF
ingress:
  type: traefik
  domain: ${DOMAIN}
  certResolver: null
storage:
  class: local-path
githubUsers: |
  u8sand
EOF
# by default, users you configure will be given a namespace and exclusive access to that namespace
# cluster admins can give the user broader access if necessary, e.g.
kubectl create clusterrolebinding u8sand --clusterrole=cluster-admin --serviceaccount=u8sand:u8sand

# any user that has been granted access can use sshkube like so:

# the client library can be configured to use the public server we've deployed
# specify your github username and github identity file
sshkube install -s ${DOMAIN} -u u8sand -i ~/.ssh/id_ed25519

# we can run commands through ssh
sshkube run kubectl get secret

# or, preferred we "activate" our environment and use local kubectl
#  this will allow us to also use port-forward
eval "$(sshkube init)"
kubectl get secret

# clean up
k3d cluster delete
```
