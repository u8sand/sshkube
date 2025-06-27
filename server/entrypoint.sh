#!/bin/sh

test -d /data/ssh-host-keys || (echo "/data must be mounted" && exit 1)

if [ ! -f /data/ssh-host-keys/etc/ssh/ssh_host_rsa_key ]; then
  echo "Generating new host keys..."
  mkdir -p /data/ssh-host-keys/etc/ssh
  ssh-keygen -A -f /data/ssh-host-keys
fi

mkdir -p /data/authorized_keys
while IFS= read -r USER; do
  curl https://github.com/${USER}.keys > /data/authorized_keys/${USER}
done < /data/users

cd /data/authorized_keys/
ls | while IFS= read -r USER; do
  # create linux user
  adduser -D -G users $USER
  passwd -d $USER
  
  # setup ssh access for user
  mkdir -p /home/$USER/.ssh/
  cp /data/authorized_keys/$USER /home/$USER/.ssh/authorized_keys
  chown -R $USER:users /home/$USER/.ssh/
  chmod 700 /home/$USER/.ssh/
  chmod 600 /home/$USER/.ssh/authorized_keys
  
  # setup kubernetes user, namespace & access for user
  kubectl apply -f - << EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${USER}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ${USER}
  namespace: ${USER}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: admin
  namespace: ${USER}
rules:
- apiGroups: ["*"]
  resources: ["*"]
  verbs: ["*"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ${USER}-admin
  namespace: ${USER}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: admin
subjects:
- kind: ServiceAccount
  name: ${USER}
  namespace: ${USER}
---
apiVersion: v1
kind: Secret
metadata:
  name: ${USER}-token
  namespace: ${USER}
  annotations:
    kubernetes.io/service-account.name: ${USER}
type: kubernetes.io/service-account-token
EOF
  # create kubeconfig for user
  CLUSTER=sshkube
  SERVER=$(kubectl cluster-info | grep 'Kubernetes control plane is running at' | awk '{print $NF}')
  CA=$(kubectl get -n ${USER} secret/${USER}-token -o=jsonpath='{.data.ca\.crt}')
  TOKEN=$(kubectl get -n ${USER} secret/${USER}-token -o=jsonpath='{.data.token}' | base64 -d)
  mkdir -p /home/${USER}/.kube
  cat > /home/${USER}/.kube/config << EOF
apiVersion: v1
kind: Config
clusters:
- name: ${CLUSTER}
  cluster:
    certificate-authority-data: ${CA}
    server: ${SERVER}
contexts:
- name: ${USER}@${CLUSTER}
  context:
    cluster: ${CLUSTER}
    namespace: ${USER}
    user: ${USER}
current-context: ${USER}@${CLUSTER}
users:
- name: ${USER}
  user:
    token: ${TOKEN}
EOF
  chown -R $USER:users /home/$USER/.kube/
  chmod 700 /home/$USER/.kube/
  chmod 600 /home/$USER/.kube/config
done

cd

$@
