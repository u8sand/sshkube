FROM alpine

VOLUME [ "/data" ]

RUN apk add --no-cache curl openrc openssh python3 kubectl \
  && sed -i \
    -e 's/^AllowTcpForwarding .\+/AllowTcpForwarding yes/' \
    -e 's/^#UseDNS .\+/UseDNS no/' \
    -e 's/^#PubkeyAuthentication .\+/PubkeyAuthentication yes/' \
    -e 's/^#PasswordAuthentication .\+/PasswordAuthentication no/' \
    -e $'$a\\\PubkeyAcceptedAlgorithms +ssh-rsa' \
    -e $'$a\\\nHostKey /data/ssh-host-keys/etc/ssh/ssh_host_rsa_key' \
    -e $'$a\\\nHostKey /data/ssh-host-keys/etc/ssh/ssh_host_ecdsa_key' \
    -e $'$a\\\nHostKey /data/ssh-host-keys/etc/ssh/ssh_host_ed25519_key' \
    -e $'$a\\\AllowGroups users' \
    /etc/ssh/sshd_config

ADD entrypoint.sh /opt/entrypoint.sh
RUN chmod +x /opt/entrypoint.sh

CMD ["/opt/entrypoint.sh", "/usr/sbin/sshd", "-D", "-e"]
