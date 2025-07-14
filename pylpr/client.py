#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
LPR client
"""

import socket
import logging
import random
import getpass
import datetime
import struct
import yaml
from typing import Union, Optional

class LprClient:
    """
    Interface for sending commands over RAW (port 9100)
    or over LPR (port 515) - RFC 1179
    """

    def __init__(self,
                 hostname: str,
                 port: Union[int, str] = 9100,
                 timeout: float = 5.0,
                 queue: str = "PASSTHRU",
                 recv_buffer: int = 4096,
                 username: Optional[str] = None,
                 job_name: Optional[str] = None,
                 file_name: Optional[str] = None,
                 label: Optional[str] = None,
                 use_reserved_port: bool = False):
        """
        Initialize LprClient instance.
        
        Args:
            hostname: Printer hostname or IP address
            port: Port number (9100 for RAW, 515 for LPR) or "LPR" string
            timeout: Socket timeout in seconds
            queue: LPR queue name (default: "PASSTHRU")
            recv_buffer: Receive buffer size
            username: Username for LPR jobs (default: current user)
            job_name: Job name for LPR jobs (default: "LprClientJob")
            file_name: File name for LPR jobs (default: job_name)
            label: Label for the job (optional, used to set the name of source file)
            use_reserved_port: Use reserved port range 721-731 (RFC 1179 requirement)
        """
        self.hostname = hostname
        self.client_hostname = socket.gethostname()

        if isinstance(port, str):
            if port.upper() == "LPR":  # RFC1179 Line printer daemon protocol
                self.port = 515
            else:  # Default to RAW if unknown string
                self.port = 9100
        else:
            self.port = port

        self.timeout = timeout
        self.recv_buffer = recv_buffer
        self.sock: Optional[socket.socket] = None
        self.queue = queue
        self.username = username or getpass.getuser()
        self.job_name = job_name or "LprClientJob"
        self.file_name = file_name or job_name
        self.label = label or None
        self.use_reserved_port = use_reserved_port

        # Define general printer sequences
        self.LF = b'\n'     # 0x0A
        self.SP = b' '      # 0x20 (space)
        self.NUL = b'\x00'  # 0x00 (null)
        self.FF = b'\x0c'   # flush buffer
        self.INITIALIZE_PRINTER = b'\x1b@'

        # Define specific Epson sequences
        self.EXIT_PACKET_MODE = b'\x00\x00\x00\x1b\x01@EJL 1284.4\n@EJL     \n'
        self.ENTER_D4 = b'\x00\x00\x00\x1b\x01@EJL 1284.4\n@EJL\n@EJL\n'
        self.REMOTE_MODE = b'\x1b' + self.remote_cmd("(R", b'\x00REMOTE1')  # ESC "(R" 08H 00H 00H "REMOTE1"
        self.ENTER_REMOTE_MODE = (
            self.INITIALIZE_PRINTER +
            self.INITIALIZE_PRINTER +
            self.REMOTE_MODE
        )
        self.EXIT_REMOTE_MODE = b'\x1b\x00\x00\x00'
        self.JOB_START = self.remote_cmd("JS", b'\x00\x00\x00\x00')
        self.JOB_END = self.remote_cmd("JE", b'\x00')
        self.PRINT_NOZZLE_CHECK = self.remote_cmd("NC", b'\x00\x00')
        self.VERSION_INFORMATION = self.remote_cmd("VI", b'\x00\x00')
        self.LD = self.remote_cmd("LD", b'')

    def __enter__(self) -> "LprClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()

    def connect(self) -> "LprClient":
        """Establish a TCP connection to the printer."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        
        # RFC 1179 requires source port to be in range 721-731 for LPR
        if self.port == 515 and self.use_reserved_port:
            # Try to bind to a reserved port (721-731) as per RFC 1179
            for source_port in range(721, 732):
                try:
                    self.sock.bind(('', source_port))
                    break
                except OSError:
                    continue
            else:
                logging.warning("Could not bind to reserved port range 721-731. "
                              "Some LPR servers may reject the connection.")
        
        self.sock.connect((self.hostname, self.port))
        return self

    def disconnect(self) -> "LprClient":
        """Shutdown and close the socket."""
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            self.sock.close()
            self.sock = None
        return self

    def send(self, data: bytes) -> "LprClient":
        """Send raw bytes to the printer."""
        if not self.sock:
            raise RuntimeError("Not connected to printer")
        if self.port == 515:  # LPR protocol
            try:
                self._print_lpr(data)
            except Exception as e:
                logging.error("LPR error: %s", e)
                self.sock.sendall(data)
        else:
            self.sock.sendall(data)
        return self

    def receive(self) -> bytes:
        """
        Receive data from the printer (up to self.recv_buffer bytes).
        Returns:
            The received data as bytes.
        """
        if not self.sock:
            raise RuntimeError("Not connected to printer")
        data = self.sock.recv(self.recv_buffer)
        return data

    def remote_cmd(self, cmd: str, args: bytes) -> bytes:
        """Generate a Remote Mode command."""
        if len(cmd) != 2:
            raise ValueError("command should be exactly 2 characters")
        return cmd.encode() + struct.pack('<H', len(args)) + args

    def set_timer(self) -> bytes:
        """Synchronize RTC by setting the current time with TI"""
        now = datetime.datetime.now()
        t_data = b'\x00'
        t_data += now.year.to_bytes(2, 'big')  # Year
        t_data += bytes([now.month, now.day, now.hour, now.minute, now.second])
        return self.remote_cmd("TI", t_data)

    def _send_lpr_command(self, command_code: int, *args: bytes, noreceive: bool=False) -> None:
        """
        Send an LPR command and wait for acknowledgment.
        
        Args:
            command_code: LPR command code (1-5)
            *args: Command arguments as bytes
        """
        if not self.sock:
            raise RuntimeError("Not connected to printer")
            
        # Build command: command_code + args (without space after command) + LF
        command = bytes([command_code])
        if args:
            command += args[0]  # First argument without space
            for arg in args[1:]:
                command += self.SP + arg  # Subsequent args separated by space
        command += self.LF
        
        logging.debug(f"Sending LPR command: {command!r}")
        self.sock.sendall(command)
        if noreceive:
            return
        
        # Wait for acknowledgment (0x00 = success, anything else = error)
        response = self.sock.recv(1)
        if response != self.NUL:
            raise ConnectionError(f"LPR command failed with response: {response!r}")

    def _generate_job_number(self) -> int:
        """Generate a unique job number between 0-999."""
        return random.randint(0, 999)

    def _create_control_file(self, data_filename: str, job_number: int) -> bytes:
        """
        Create RFC 1179 compliant control file content.
        
        Args:
            data_filename: Name of the data file
            job_number: Job number (0-999)
            
        Returns:
            Control file content as bytes
        """
        control_lines = []
        
        # Required fields per RFC 1179
        control_lines.append(f"H{self.client_hostname}")  # Host name (required)
        control_lines.append(f"P{self.username}")         # User identification (required)
        
        # Optional fields
        if self.job_name:
            control_lines.append(f"J{self.job_name}")         # Job name for banner page
        if self.client_hostname:
            control_lines.append(f"C{self.client_hostname}")  # Class for banner page
        if self.file_name and not self.label:
            control_lines.append(f"N{self.file_name}")        # Name of source file
        if self.label:
            control_lines.append(f"N{self.label}")            # Use label to set the name of source file
        
        # At least one lowercase command is required for output
        control_lines.append(f"l{data_filename}")         # Print file leaving control characters
        
        # Unlink data file after printing
        control_lines.append(f"U{data_filename}")
        
        # Join lines with LF and add final LF
        control_content = self.LF.join(line.encode('ascii') for line in control_lines) + self.LF
        
        logging.debug(f"Control file content:\n{control_content.decode('ascii', errors='replace')}")
        return control_content

    def _print_lpr(self, data: bytes, job_title: str = None) -> None:
        """
        Send a print job using RFC 1179 compliant LPR protocol.
        
        Args:
            data: Raw print data
            job_title: Optional job title (defaults to self.job_name)
        """
        if job_title:
            original_job_name = self.job_name
            self.job_name = job_title
        
        try:
            # Generate unique job number (0-999)
            job_number = self._generate_job_number()
            
            # Generate file names per RFC 1179
            # Control file: cfA + 3-digit job number + hostname
            # Data file: dfA + 3-digit job number + hostname
            control_filename = f"cfA{job_number:03d}{self.client_hostname}"
            data_filename = f"dfA{job_number:03d}{self.client_hostname}"
            
            logging.debug(f"LPR Job {job_number}: Control file={control_filename}, Data file={data_filename}")
            
            # Step 1: Send "Receive a printer job" command (code 02) without space after command
            self._send_lpr_command(2, self.queue.encode('ascii'))
            
            # Step 2: Create and send control file
            control_content = self._create_control_file(data_filename, job_number)
            
            # Send "Receive control file" subcommand (code 02) with correct spacing
            self._send_lpr_command(2, str(len(control_content)).encode('ascii'), 
                                 control_filename.encode('ascii'))
            
            # Send control file content + NUL terminator
            self.sock.sendall(control_content + self.NUL)
            
            # Wait for acknowledgment of control file
            response = self.sock.recv(1)
            if response != self.NUL:
                raise ConnectionError(f"Control file rejected: {response!r}")
            
            # Step 3: Send data file
            # Send "Receive data file" subcommand (code 03) with correct spacing
            self._send_lpr_command(3, str(len(data)).encode('ascii'), 
                                 data_filename.encode('ascii'))
            
            # Send data file content + NUL terminator
            self.sock.sendall(data + self.NUL)
            
            # Wait for acknowledgment of data file
            response = self.sock.recv(1)
            if response != self.NUL:
                raise ConnectionError(f"Data file rejected: {response!r}")
            
            logging.info(f"LPR job {job_number} submitted successfully")
            
        except Exception as e:
            logging.error(f"LPR printing failed: {e}")
            # Try to send abort command if connection is still active
            try:
                if self.sock:
                    # Correct abort command format: \001 + queue name + LF
                    abort_cmd = b'\x01' + self.queue.encode('ascii') + self.LF
                    self.sock.sendall(abort_cmd)
            except:
                pass
            raise
        finally:
            # Restore original job name if it was temporarily changed
            if job_title:
                self.job_name = original_job_name

def main():
    import argparse
    from pprint import pprint
    import os
    from pathlib import Path

    def auto_port(port):
        if isinstance(port, str):
            if port.upper() == "LPR":  # RFC1179 Line printer daemon protocol
                return 515
            elif port.upper() == "RAW":
                return 9100
            return int(port, 0)  # e.g. "9100" or "0x23"
        return int(port)  # already numeric

    parser = argparse.ArgumentParser(
        prog="pylpr client",
        description='RAW and LPR print client.',
        epilog='RAW/LPR print client.'
    )

    parser.add_argument(
        '-a',
        '--address',
        dest='hostname',
        action="store",
        help='Printer host name or IP address. (Example: -a 192.168.1.87)',
        required=True,
        metavar='ADDRESS'
    )
    parser.add_argument(
        '-p',
        '--port',
        dest='port',
        type=auto_port,
        default=515,  # LPR
        action="store",
        help='Printer port (default is "LPR" = 515)'
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '-P',
        '--print',
        dest='print_queue',
        action='store_true',
        help='Send the "Print any waiting jobs" command'
        ' (RFC1179 0x01) to the specified queue and exit.'
    )
    group.add_argument(
        '-s',
        '--status',
        dest='status',
        action='store',
        type=str,
        default=None,
        metavar='LIST',
        help='Send queue state (short) command with "list"'
        '  attribute (RFC1179 0x03) to the specified queue'
        ' and print the result.'
    )
    group.add_argument(
        '-S',
        '--longstatus',
        dest='longstatus',
        action='store',
        type=str,
        default=None,
        metavar='LIST',
        help='Send queue state (long) command with "list"'
        ' attribute (RFC1179 0x04) to the specified queue'
        ' and print the result.'
    )
    group.add_argument(
        '-R',
        '--remove',
        dest='remove',
        action='store',
        type=str,
        default=None,
        metavar='LIST',
        help='Remove jobs command with "list"'
        ' attribute (RFC1179 0x05) to the specified queue.'
    )
    group.add_argument(
        '-f',
        "--file",
        dest='print_file',
        type=argparse.FileType('r'),
        help="File to be printed",
        metavar='PRINT_FILE'
    )
    parser.add_argument(
        '-d',
        '--debug',
        dest='debug',
        action='store_true',
        help='Print debug information'
    )
    parser.add_argument(
        '-e',
        '--epson',
        dest='epson',
        action='store_true',
        help='Use Epson header and footer'
    )
    parser.add_argument(
        '-q',
        '--queue',
        dest='queue',
        action='store',
        type=str,
        default="PASSTHRU",
        metavar='QUEUE',
        help='Queue name; default queue name is "PASSTHRU"'
    )
    parser.add_argument(
        '-r',
        '--reserved',
        dest='use_reserved_port',
        action='store_true',
        help='Use reserved port range 721-731 (RFC 1179 requirement)'
    )
    parser.add_argument(
        '-t',
        '--timeout',
        dest='timeout',
        type=float,
        default=5.0,
        help='Timeout (default: 5.0 seconds)',
    )
    parser.add_argument(
        '-b',
        '--buffer',
        dest='recv_buffer',
        type=int,
        default=4096,
        help='Receive buffer size (default: 4096 bytes)',
    )
    parser.add_argument(
        '-u',
        '--username',
        dest='username',
        action='store',
        type=str,
        default=None,
        metavar='USERNAME',
        help='User name (default: current user)'
    )
    parser.add_argument(
        '-l',
        '--label',
        dest='label',
        action='store',
        type=str,
        default=None,
        metavar='LABEL',
        help='Label for the job (optional, used to set the name of source file)'
    )
    parser.add_argument(
        '-j',
        '--job',
        dest='job_name',
        action='store',
        default="LprClientJob",
        type=str,
        metavar='JOB_NAME',
        help='Job name (default: "LprClientJob")'
    )

    args = parser.parse_args()

    logging_level = logging.WARNING
    logging_fmt = "%(message)s"
    env_key=os.path.basename(Path(__file__).stem).upper() + '_LOG_CFG'
    path = Path(__file__).stem + '-log.yaml'
    value = os.getenv(env_key, None)
    #print("Configuration file:", path, "| Environment variable:", env_key)
    if value:
        path = value
    if os.path.exists(path):
        with open(path, 'rt') as f:
            config = yaml.safe_load(f.read())
        try:
            logging.config.dictConfig(config)
        except Exception as e:
            logging.basicConfig(level=logging_level, format=logging_fmt)
            logging.critical("Cannot configure logs: %s. %s", e, path)
    else:
        logging.basicConfig(level=logging_level, format=logging_fmt)

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.status and not args.queue:
        parser.error("You must specify a queue name with -w/--status option.")
    if args.longstatus and not args.queue:
        parser.error("You must specify a queue name with -S/--longstatus option.")
    if args.print_file and not args.print_file.name:
        parser.error("You must specify a file to print with -f/--file option.")
    if args.print_queue and args.print_file:
        parser.error("You cannot use -P/--print and -f/--file options together.")

    try:
        with LprClient(
            hostname=args.hostname,
            port=args.port,
            timeout=args.timeout,
            queue=args.queue,
            recv_buffer=args.recv_buffer,
            username=args.username,
            job_name=args.job_name,
            file_name=getattr(args.print_file, 'name', None) if hasattr(args, 'print_file') and args.print_file else None,
            label=args.label,
            use_reserved_port=args.use_reserved_port
        ) as lpr:
            if args.print_queue:
                # Send the 'Print any waiting jobs' command (0x01) to the specified queue
                lpr._send_lpr_command(1, args.queue.encode('ascii'))
                print(f"Sent 'Print any waiting jobs' command to queue '{args.queue}'")
                return
            if args.status:
                lpr._send_lpr_command(3, (args.queue + " " + args.status).encode('ascii'), noreceive=True)
                print(f"Sent 'Send queue state (short)' command with attributes '{args.status}' to queue '{args.queue}'")
                # Receive and print the response from the printer
                response = lpr.receive()
                print("Received queue state (short) response:")
                print(response.decode('utf-8', errors='replace'))
                return
            if args.longstatus:
                lpr._send_lpr_command(4, (args.queue + " " + args.longstatus).encode('ascii'), noreceive=True)
                print(f"Sent 'Send queue state (long)' command with attributes '{args.longstatus}' to queue '{args.queue}'")
                # Receive and print the response from the printer
                response = lpr.receive()
                print("Received queue state (long) response:")
                print(response.decode('utf-8', errors='replace'))
                return
            if args.remove:
                # Send the 'Remove jobs' command (0x05) to the specified queue
                lpr._send_lpr_command(5, (args.queue + " " + args.remove).encode('ascii'))
                print(f"Sent 'Remove jobs' command with attributes '{args.remove}' to queue '{args.queue}'")
                return
            if args.epson:
                lpr.send(
                    lpr.EXIT_PACKET_MODE
                    + lpr.INITIALIZE_PRINTER
                    + args.print_file.read().encode('utf-8')
                    + lpr.FF
                )
            else:
                lpr.send(args.print_file.read().encode('utf-8'))
    except KeyboardInterrupt:
        quit(2)
    except Exception as e:
        logging.error(f"Printer LPR failed: {str(e)}")

if __name__ == "__main__":
    main()
