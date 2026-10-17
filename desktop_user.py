"""Run desktop workers as the original graphical user."""
import os
from core import ROOT

def sender_command(uid):
    import pwd
    user = pwd.getpwuid(uid)
    env = {'HOME':user.pw_dir, 'USER':user.pw_name, 'LOGNAME':user.pw_name,
           'PATH':'/usr/local/bin:/usr/bin:/bin', 'PYTHONUTF8':'1',
           'XDG_RUNTIME_DIR':'/run/user/%d' % uid}
    for name in ('DISPLAY','XAUTHORITY','DBUS_SESSION_BUS_ADDRESS','AT_SPI_BUS_ADDRESS','XDG_SESSION_TYPE'):
        if name in os.environ:
            env[name] = os.environ[name]
    env.setdefault('DBUS_SESSION_BUS_ADDRESS', 'unix:path=/run/user/%d/bus' % uid)
    command = ['/usr/bin/sudo','-u',user.pw_name,'--', '/usr/bin/env']
    command += ['%s=%s' % item for item in env.items()]
    command += [str(ROOT/'.venv/bin/python'), '-u', str(ROOT/'multi_window_sender.py')]
    return command
