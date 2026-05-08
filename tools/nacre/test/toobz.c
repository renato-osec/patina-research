struct std_os_fd_OwnedFd {
    int32_t fd;
};

struct std_sys_pal_unix_fd_FileDesc {
    struct std_os_fd_OwnedFd _0;
};

struct std_sys_pal_unix_net_Socket {
    struct std_sys_pal_unix_fd_FileDesc _0;
};

struct std_sys_common_net_TcpListener {
    struct std_sys_pal_unix_net_Socket inner;
};

struct std_net_TcpListener {
    struct std_sys_common_net_TcpListener _0;
};

struct TSuck {
    struct std_net_TcpListener sucker;
};

