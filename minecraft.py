import asyncio
import multiprocessing as mp
import multiprocessing.connection as mpc
import os
import subprocess as sp
import threading
import time

from dotenv import load_dotenv

__all__ = ['Minecraft']

# Load Env
load_dotenv()
SECRET = str.encode(os.getenv('SECRET'))
BOT_CHAN_ID = int(os.getenv('BOT_CHAN_ID'))
MC_LOG_CHAN_ID = int(os.getenv('MC_LOG_CHAN_ID'))
MC_DIR = os.getenv('MC_DIR')
MCC_PORT = int(os.getenv('MCC_PORT'))

# Globals
proc = None
conn = None
address = ('localhost', MCC_PORT)

# This will be the class we export and the serverbot will import
class Minecraft:
    def __init__(self,
                 client,
                 guild,
                 prefix='mc',
                 port=MCC_PORT,
                 botchanid=BOT_CHAN_ID,
                 logchanid=MC_LOG_CHAN_ID):
        # Set up members
        self.prefix = prefix
        self.port = port
        self.guild = guild
        self.client = client
        self.logchan = guild.get_channel(logchanid)
        self.botchan = guild.get_channel(botchanid)
        self.__conn = None
        # Launch the read thread. This will attempt to create a connection to a mc server controller and listen
        def read_thread():
            while True:
                try:
                    self.__conn = mpc.Client(('localhost', port), authkey=SECRET)
                    self.__botchan_send('Minecraft server manager connected!')
                except (EOFError, ConnectionRefusedError, ConnectionResetError, BrokenPipeError):
                    if self.__conn is not None:
                        self.__conn.close()
                        time.sleep(10) # Wait a reasonable amount of time and chek again
                while self.__conn and (not self.__conn.closed):
                    try:
                        line = self.__conn.recv()
                        [status, msg] = line.split('|', 1)
                        status = status.strip()
                        if status == 'LOG':
                            self.__logchan_send(msg)
                        elif status == 'OK':
                            self.__botchan_send(msg)
                        else:
                            self.__botchan_send(f'{status}: {msg}')
                    except (EOFError, ConnectionResetError, BrokenPipeError):
                        self.__botchan_send('ERR: The Minecraft server manager crashed. Attempting to reconnect')
                        self.__conn.close()

        reader = threading.Thread(target=read_thread)
        reader.daemon = True
        reader.start()

    def try_send(self, msg):
        #TODO: handle errors
        try:
            self.__conn.send(msg)
        except (OSError, AttributeError):
            # We lost connection. We'll just log it and let the read loop handle reconnecting
            self.__botchan_send('Could not send command to Minecraft server manager')

    def __logchan_send(self, msg):
        asyncio.run_coroutine_threadsafe(self.logchan.send(msg), self.client.loop)

    def __botchan_send(self, msg):
        asyncio.run_coroutine_threadsafe(self.botchan.send(msg), self.client.loop)



def mc_running():
    return proc and proc.poll() is None


def try_send(msg):
    try:
        conn.send(msg + '\n')
    except (OSError, AttributeError):
        # Since we lost connection to the client we can't really notify them there's an issues so
        # just log it and fail
        print(f'TRYSEND: Failed to send: {msg}')


def mc_write(cmd):
    try:
        proc.stdin.write(str.encode(cmd + '\n'))
        proc.stdin.flush()
    except AttributeError:
        print(f'MCSEND: Server is dead')


def mc_start():
    """
    OK basically we're gonna start a minecraft process and create a listener thread dedicated to it.
    """
    global proc

    if mc_running():
        return False
    else:
        proc = sp.Popen(['java', '-Xmx1024M', '-Xms1024M', '-jar', 'server.jar', 'nogui'],
                        stdin=sp.PIPE,
                        stdout=sp.PIPE,
                        stderr=sp.STDOUT,
                        cwd=MC_DIR)
        # TODO verify this actually started successfully

        # Start a reader for this process
        go = threading.Event()
        def read_thread():
            line = None
            while mc_running():
                # Grab a new line if we're not holding onto a failed send
                if not line:
                    # Try reading a line. If this fails, check that the proc didn't die
                    try:
                        line = proc.stdout.readline()
                    except BrokenPipeError:
                        print('READER: Pipe read failed!')
                        continue # Top loop will handle dead process, otherwise we retry
                # Check that we have something to send
                if line:
                    # Wait for a connection to be established
                    while not conn or conn.closed:
                        time.sleep(10) # wait for the connection to come back up
                    # Try to send the thing
                    try:
                        conn.send(f'LOG |{bytes.decode(line)}')
                        line = None
                    # If we fail, close the connection (remote probably disconnected) and leave the
                    # line so we can retry it
                    except OSError:
                        print('READER: Client disconnected!')
                        conn.close()
            print('READER: Process exited. Exiting reader thread.')

        reader = threading.Thread(target=read_thread)
        reader.daemon = True
        reader.start()

        return True


def mc_stop():
    global proc

    if not mc_running():
        return False
    else:
        mc_write('stop')
        # wait to stop
        while proc.poll() is None:
            time.sleep(1)
        proc = None
        return True


def mc_whitelist(name, add):
    if not mc_running():
        return False
    else:
        if add:
            mc_write(f'whitelist add {name}')
            mc_write('whitelist reload')
        else:
            mc_write(f'whitelist remove {name}')
            mc_write('whitelist reload')
        return True


def mc_ls_whitelist():
    if not mc_running():
        return False
    else:
        mc_write('whitelist list')
        return True


def mc_command(cmd, args):
    print(f'CMD: {cmd} {args}')
    help_msg = ('ServerBot Minecraft commands:\n'
                '!mc help - print this message\n'
                '!mc ping - ping the server\n'
                '!mc status - check the server status\n'
                '!mc start - start the server\n'
                '!mc stop - stop the server\n'
                '!mc whitelist <add|remove|list> [player] - list or modify the whitelist')
#                '!mc cmd <command> - send command to the server\n'
    if cmd == 'help':
        try_send(f'OK  |{help_msg}')
    elif cmd == 'start':
        result = mc_start()
        if result:
            try_send('OK  |Minecraft server starting')
        else:
            try_send('ERR |Minecraft server is already running')
    elif cmd == 'stop':
        result = mc_stop()
        if result:
            try_send('OK  |Minecraft server stopped')
        else:
            try_send('ERR |Minecraft Server is not running')
    elif cmd == 'ping':
        try_send(f'OK  |pong')
    elif cmd == 'status':
        if mc_running():
            try_send('OK  |Minecraft Server is running')
        else:
            try_send('OK  |Minecraft Server is not running')
    elif cmd == 'whitelist':
        if args:
            arglist = args.split()
            wl_cmd = arglist[0]
            wl_name = None
            if len(arglist) == 2:
                wl_name = arglist[1]
            if wl_cmd == 'list':
                result = mc_ls_whitelist()
                if result:
                    try_send('OK  |Success - check the log for current whitelist')
                else:
                    try_send('ERR |Minecraft Server is not running')
                return
            if wl_cmd == 'add' and wl_name:
                result = mc_whitelist(wl_name, True)
                if result:
                    try_send('OK  |User added to whitelist')
                else:
                    try_send('ERR |Minecraft Server is not running')
                return
            elif wl_cmd == 'remove' and wl_name:
                result = mc_whitelist(wl_name, False)
                if result:
                    try_send('OK  |User removed from whitelist')
                else:
                    try_send('ERR |Minecraft Server is not running')
                return
        # We didn't hit any valid cases
        try_send(f'ERR |Usage: !mc whitelist <add|remove|list> [player]')

#    elif cmd == 'cmd':
#        if proc:
#            mc_write(args)
#            try_send('OK  |')
#        else:
#            try_send('ERR |Minecraft Server is not running')
    else:
        try_send(f'ERR |Unknown command: {cmd}')
        try_send(f'OK  |{help_msg}')

if __name__ == '__main__':
    # Open IPC channel
    listener = mpc.Listener(address, authkey=SECRET)

    # Wait for connections
    while True:
        try:
            conn = listener.accept()
        except (EOFError, ConnectionResetError, BrokenPipeError):
            print('LISTENER: Failed to connect to client')
            continue
        print('LISTENER: Client connected!')
        while conn and (not conn.closed):
            try:
                line = conn.recv()
                tokens = line.split(' ', 1)
                cmd = tokens[0]
                args = None
                if len(tokens) > 1:
                    args = tokens[1].rstrip()
                mc_command(cmd, args)
            except (EOFError, ConnectionResetError, BrokenPipeError):
                print(f'LISTENER: Client disconnected!')
                conn.close()

 # TODO: detect crash and alert back to discord
