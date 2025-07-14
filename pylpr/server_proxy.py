#!/usr/bin/env python3
import logging
import asyncio
import argparse
import platform
import os
import time
import sys
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict, Any
from pathlib import Path
import yaml
from hexdump2 import hexdump, color_always

EXCLUDED_TRACE_PORTS = set()


@dataclass(frozen=True)
class PortMapping:
    port: int
    remote_host: Optional[str]
    remote_port: Optional[int]

@dataclass
class LPRJob:
    job_id: str
    user: str
    host: str
    queue: str
    control_file: Optional[bytes] = None
    data_file: Optional[bytes] = None
    control_filename: Optional[str] = None
    data_filename: Optional[str] = None
    status: str = "pending"
    timestamp: float = 0.0
    
    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

class LprServer:
    def __init__(self, save_files: bool = False, save_path: str = None):
        if save_path is None:
            save_path = "lpr_jobs"
        self.jobs: Dict[str, LPRJob] = {}
        self.job_counter = 0
        self.save_files = save_files
        self.save_dir = Path(save_path)
        if self.save_files:
            self.save_dir.mkdir(exist_ok=True)
    
    def get_next_job_id(self) -> str:
        """Generate next job ID (000-999)"""
        job_id = f"{self.job_counter:03d}"
        self.job_counter = (self.job_counter + 1) % 1000
        return job_id
    
    def parse_control_filename(self, filename: str) -> Tuple[Optional[str], Optional[str]]:
        """Parse control file name according to RFC 1179"""
        # Control file name format: cfA###hostname
        # where ### is a three-digit job number
        if len(filename) < 6 or not filename.startswith("cfA"):
            return None, None
        
        try:
            job_number = filename[3:6]  # Extract 3-digit job number
            hostname = filename[6:]     # Extract hostname
            return job_number, hostname
        except:
            return None, None
    
    def parse_data_filename(self, filename: str) -> Tuple[Optional[str], Optional[str]]:
        """Parse data file name according to RFC 1179"""
        # Data file name format: dfA###hostname
        # where ### is a three-digit job number
        if len(filename) < 6 or not filename.startswith("dfA"):
            return None, None
        
        try:
            job_number = filename[3:6]  # Extract 3-digit job number
            hostname = filename[6:]     # Extract hostname
            return job_number, hostname
        except:
            return None, None
    
    def parse_control_file(self, content: bytes, log: bool = True) -> Dict[str, Any]:
        """Parse LPR control file according to RFC 1179 section 7"""
        control_data = {}
        control_info = {
            'raw_commands': [],
            'parsed_commands': {}
        }
        
        try:
            lines = content.decode('ascii').split('\n')
            for line in lines:
                if len(line) < 1:
                    continue
                
                cmd = line[0]
                operand = line[1:] if len(line) > 1 else ""
                
                control_info['raw_commands'].append({'command': cmd, 'operand': operand})
                
                # Store all control file commands
                if cmd not in control_info['parsed_commands']:
                    control_info['parsed_commands'][cmd] = []
                control_info['parsed_commands'][cmd].append(operand)
                
                # Parse specific commands according to RFC 1179
                command_desc = self.get_control_command_description(cmd, operand)
                if log:
                    logging.info(f"    Control command: {cmd} → {command_desc}")
                
        except UnicodeDecodeError:
            logging.warning(
                "Warning: Control file contains non-ASCII characters"
            )
        
        return control_info
    
    def get_control_command_description(self, cmd: str, operand: str) -> str:
        """Get description of control file command according to RFC 1179"""
        commands = {
            'H': f"H - Host name: {operand}",
            'P': f"P - User identification: {operand}",
            'J': f"J - Job name for banner page: {operand}",
            'C': f"C - Class for banner page: {operand}",
            'L': f"L - Literal user's name for banner: {operand}",
            'T': f"T - Title for pr: {operand}",
            'I': f"I - Indent printing (columns): {operand}",
            'M': f"M - Mail when printed: {operand}",
            'f': f"f - Print file (text): {operand}",
            'l': f"l - Print file (raw - no processing): {operand}",
            'o': f"o - Print file (PostScript): {operand}",
            'p': f"p - Print file (formatted with pr): {operand}",
            'r': f"r - Print file (FORTRAN carriage control): {operand}",
            't': f"t - Print file (troff): {operand}",
            'n': f"n - Print file (ditroff): {operand}",
            'd': f"d - Print file (DVI): {operand}",
            'g': f"g - Print file (plot): {operand}",
            'v': f"v - Print file (Sun raster): {operand}",
            'c': f"c - Print file (cifplot): {operand}",
            'z': f"z - Print file (other format): {operand}",
            'U': f"U - Unlink data file: {operand}",
            'N': f"N - File name: {operand}",
            'W': f"W - Width of output: {operand}",
            '#': f"# - Number of copies: {operand}",
            'S': f"S - Symbolic link data: {operand}",
            '1': f"1 - TROFF font R: {operand}",
            '2': f"2 - TROFF font I: {operand}",
            '3': f"3 - TROFF font B: {operand}",
            '4': f"4 - TROFF font S: {operand}",
        }
        
        return commands.get(cmd, f"Unknown command '{cmd}': {operand}")
    
    def format_queue_status(self, queue: str, long_format: bool = False) -> str:
        """
        Format queue status response according to RFC 2569 (Send queue state short/long).
        Handles multiple files and multiple copies per job as per RFC 2569.
        """
        queue_dir = self.save_dir / queue
        if not queue_dir.exists() or not queue_dir.is_dir():
            return f"no entries in queue {queue}\n"
        job_dirs = [d for d in queue_dir.iterdir() if d.is_dir()]
        job_dirs.sort(key=lambda d: d.stat().st_mtime)
        status_lines = []
        if not job_dirs:
            return f"no entries in queue {queue}\n"
        if long_format:
            status_lines.append(f"{queue} is ready and printing")
            rank_names = ["active", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th"]
            PRINT_FILE_CMDS = ['f','l','o','p','r','t','n','d','g','v','c','z']
            for idx, job_dir in enumerate(job_dirs):
                control_files = list(job_dir.glob("cfA*"))
                if not control_files:
                    continue
                control_file = control_files[0]
                try:
                    with open(control_file, 'rb') as f:
                        content = f.read()
                    control_info = self.parse_control_file(content, log=False)
                    parsed = control_info.get('parsed_commands', {})
                    user = parsed.get('P', ['unknown'])[0]
                    parts = job_dir.name.split('_')
                    if len(parts) >= 3 and parts[0] == "job":
                        job_number = parts[1]
                    else:
                        job_number = job_dir.name
                    hostname = parsed.get('H', ['unknown'])[0]
                    rank = rank_names[idx] if idx < len(rank_names) else f"{idx+1}th"
                    left = f"{user}: {rank}"
                    pad = 40 - len(left)
                    if pad < 1:
                        pad = 1
                    header = f"\n{left}{' ' * pad}[job {job_number} {hostname}]"
                    status_lines.append(header)
                    # --- RFC 2569: handle multiple files and copies ---
                    # Collect all print file commands
                    printfile_lines = []
                    for cmd in PRINT_FILE_CMDS:
                        printfile_lines.extend(parsed.get(cmd, []))
                    n_lines = parsed.get('N', [])
                    from collections import Counter
                    pf_counter = Counter(printfile_lines)
                    datafile_to_filename = {}
                    n_idx = 0
                    for i, pf_name in enumerate(printfile_lines):
                        if pf_name not in datafile_to_filename and n_idx < len(n_lines):
                            datafile_to_filename[pf_name] = n_lines[n_idx]
                            n_idx += 1
                    # If no files, still show a line for single file jobs
                    if not pf_counter and n_lines:
                        for i, n_file in enumerate(n_lines):
                            data_file_path = job_dir / n_file
                            try:
                                size = data_file_path.stat().st_size
                            except Exception:
                                size = 0
                            left_part = f"        {n_file}"
                            pad = 40 - len(left_part)
                            if pad < 1:
                                pad = 1
                            line = f"{left_part}{' ' * pad}{size} bytes"
                            status_lines.append(line)
                    else:
                        for pf_name, copies in pf_counter.items():
                            filename = datafile_to_filename.get(pf_name, pf_name)
                            data_file_path = job_dir / pf_name
                            try:
                                size = data_file_path.stat().st_size
                            except Exception:
                                size = 0
                            left_part = f"        {copies} copies of {filename}" if copies > 1 else f"        {filename}"
                            pad = 40 - len(left_part)
                            if pad < 1:
                                pad = 1
                            line = f"{left_part}{' ' * pad}{size} bytes"
                            status_lines.append(line)
                except Exception as e:
                    status_lines.append(f"{job_dir.name}: error reading control file: {e}")
        else:
            # RFC 2569: Short format with columns and Rank
            header = (
                f"{'Rank':<7}"
                f"{'Owner':<11}"
                f"{'Job':<16}"
                f"{'File(s)':<28}"
                f"{'Total Size':<12}"
            )
            status_lines.append(f"{queue} is ready and printing")
            status_lines.append(header)
            rank_names = ["active", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th"]
            PRINT_FILE_CMDS = ['f','l','o','p','r','t','n','d','g','v','c','z']
            for idx, job_dir in enumerate(job_dirs):
                control_files = list(job_dir.glob("cfA*"))
                if not control_files:
                    continue
                control_file = control_files[0]
                try:
                    with open(control_file, 'rb') as f:
                        content = f.read()
                    control_info = self.parse_control_file(content, log=False)
                    parsed = control_info.get('parsed_commands', {})
                    user = parsed.get('P', ['unknown'])[0]
                    parts = job_dir.name.split('_')
                    if len(parts) >= 3 and parts[0] == "job":
                        job_number = parts[1]
                        job_name = job_number
                    else:
                        job_name = job_dir.name
                    # Collect all print file commands
                    printfile_lines = []
                    for cmd in PRINT_FILE_CMDS:
                        printfile_lines.extend(parsed.get(cmd, []))
                    n_lines = parsed.get('N', [])
                    from collections import Counter
                    pf_counter = Counter(printfile_lines)
                    datafile_to_filename = {}
                    n_idx = 0
                    for i, pf_name in enumerate(printfile_lines):
                        if pf_name not in datafile_to_filename and n_idx < len(n_lines):
                            datafile_to_filename[pf_name] = n_lines[n_idx]
                            n_idx += 1
                    file_list = []
                    total_size = 0
                    if not pf_counter and n_lines:
                        for i, n_file in enumerate(n_lines):
                            data_file_path = job_dir / n_file
                            try:
                                size = data_file_path.stat().st_size
                            except Exception:
                                size = 0
                            file_list.append(n_file)
                            total_size += size
                    else:
                        for pf_name, copies in pf_counter.items():
                            filename = datafile_to_filename.get(pf_name, pf_name)
                            data_file_path = job_dir / pf_name
                            try:
                                size = data_file_path.stat().st_size
                            except Exception:
                                size = 0
                            total_size += size * copies
                            if copies > 1:
                                file_list.append(f"{copies}x {filename}")
                            else:
                                file_list.append(filename)
                    files_str = ', '.join(file_list)[:28]
                    size_str = f"{total_size} bytes"
                    rank = rank_names[idx] if idx < len(rank_names) else f"{idx+1}th"
                    line = (
                        f"{rank:<7}"
                        f"{user:<11}"
                        f"{job_name:<16}"
                        f"{files_str:<28}"
                        f"{size_str:<12}"
                    )
                    status_lines.append(line)
                except Exception as e:
                    status_lines.append(f"{job_dir.name}: error reading control file: {e}")
        return '\n'.join(status_lines) + '\n'
    
    def save_job_files(self, job: LPRJob):
        """Save job files to disk"""
        base_name = f"job_{job.job_number}_{job.user}"
        job_dir = self.save_dir / job.queue / base_name
        counter = 1

        while job_dir.exists():
            job_dir = self.save_dir / f"{base_name}_{counter}"
            counter += 1
        job_dir.mkdir(parents=True, exist_ok=True)
        
        if job.control_file:
            control_path = job_dir / (job.control_filename or f"cf{job.job_id}")
            with open(control_path, 'wb') as f:
                f.write(job.control_file)
        
        if job.data_file:
            data_path = job_dir / (job.data_filename or f"df{job.job_id}")
            with open(data_path, 'wb') as f:
                f.write(job.data_file)

    def list_jobs_in_queue(self, queue: str) -> str:
        """
        Travel all jobs in the indicated queue, sorted by job directory date,
        decoding the control file, with command descriptions, and print them.
        Also print the size and copies of all data files in the job.
        """
        queue_dir = self.save_dir / queue
        if not queue_dir.exists() or not queue_dir.is_dir():
            return f"No jobs found in queue '{queue}'."
        job_dirs = [d for d in queue_dir.iterdir() if d.is_dir()]
        job_dirs.sort(key=lambda d: d.stat().st_mtime)
        result = [f"Jobs in queue '{queue}':\n"]
        for job_dir in job_dirs:
            control_files = list(job_dir.glob("cfA*"))
            if not control_files:
                continue
            control_file = control_files[0]
            try:
                with open(control_file, 'rb') as f:
                    content = f.read()
                control_info = self.parse_control_file(content, log=False)
                job_info = f"- Job: {job_dir.name}\n  Control file: {control_file.name}\n"
                for cmd in control_info.get('raw_commands', []):
                    desc = self.get_control_command_description(cmd['command'], cmd['operand'])
                    job_info += f"    {desc}\n"
                # After control file dump, print all data files and their copies
                parsed = control_info.get('parsed_commands', {})
                f_lines = parsed.get('f', [])
                n_lines = parsed.get('N', [])
                from collections import Counter
                f_counter = Counter(f_lines)
                datafile_to_filename = {}
                n_idx = 0
                for i, f_name in enumerate(f_lines):
                    if f_name not in datafile_to_filename and n_idx < len(n_lines):
                        datafile_to_filename[f_name] = n_lines[n_idx]
                        n_idx += 1
                for f_name, copies in f_counter.items():
                    filename = datafile_to_filename.get(f_name, f_name)
                    data_file_path = job_dir / f_name
                    try:
                        size = data_file_path.stat().st_size
                    except Exception:
                        size = 0
                    copies_str = f"{copies} copies of " if copies > 1 else ""
                    job_info += f"  Data file: {copies_str}{filename} ({size} bytes)\n"
                result.append(job_info)
            except Exception as e:
                result.append(f"Job: {job_dir.name} (error reading control file: {e})")
        if len(result) == 1:
            return f"No jobs found in queue '{queue}'."
        return '\n'.join(result)


# Static replacements configuration
REPLACEMENTS: dict[int, List[Tuple[bytes, bytes]]] = {
    9000: [(b'foo', b'bar')]
}

def trace_data(data: bytes, direction: str, port: int, enabled: bool, description: str = ""):
    """Enhanced trace function with protocol command descriptions"""
    if not enabled or not data or port in EXCLUDED_TRACE_PORTS:
        return
    
    desc_str = f" - {description}" if description else ""
    logging.warning(f"\n[{direction} port {port}]{desc_str}")
    color_always()
    hexdump(data)

def describe_lpr_command(data: bytes) -> str:
    """
    Return a text describing the LPR protocol commands
    according to RFC 1179
    """
    if not data:
        return "Empty data"
    
    cmd = data[0]
    
    # Main daemon commands (section 5 of RFC1179)
    if cmd == 0x01:
        return "CMD: Print any waiting jobs"
    elif cmd == 0x02:
        # Could be "Receive printer job" or "Receive control file"
        if len(data) >= 2 and data[-1] == 0x0A:
            body = data[1:-1]
            if b' ' in body:
                parts = body.split(b' ', 1)
                if parts[0].isdigit():
                    return "SUBCMD: Receive control file"
            return "CMD: Receive a printer job"
        else:
            return "CMD: Receive a printer job (malformed?)"
    elif cmd == 0x03:
        # Could be "Send queue state (short)" or "Receive data file"
        if len(data) >= 2 and data[-1] == 0x0A:
            body = data[1:-1]
            if b' ' in body:
                parts = body.split(b' ', 1)
                if parts[0].isdigit():
                    return "SUBCMD: Receive data file"
            return "CMD: Send queue state (short)"
        else:
            return "CMD: Send queue state (short) (malformed?)"
    elif cmd == 0x04:
        return "CMD: Send queue state (long)"
    elif cmd == 0x05:
        return "CMD: Remove jobs"
    
    # Receive job subcommands (section 6)
    elif cmd == 0x01 and len(data) == 2 and data[1] == 0x0A:
        return "SUBCMD: Abort job"
    
    # Response codes
    elif cmd == 0x00:
        return "RESPONSE: ACK (positive acknowledgment)"
    elif cmd != 0x00 and len(data) == 1:
        return f"RESPONSE: NAK (negative acknowledgment: {cmd})"
    
    return f"Unknown command/data (first byte: 0x{cmd:02x})"

async def handle_lpr_protocol(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    port: int,
    trace: bool,
    lpr_server: LprServer
) -> None:
    """Handle LPR protocol according to RFC 1179"""
    
    current_job = None
    job_state = "waiting"  # "waiting", "receiving_job_initial", "receiving_job", "complete"
    
    try:
        while True:
            # Read command
            data = await reader.read(4096)
            if not data:
                break
            
            # Apply replacements if configured
            for old, new in REPLACEMENTS.get(port, []):
                data = data.replace(old, new)
            
            description = describe_lpr_command(data)
            trace_data(data, "client → server", port, trace, description)
            
            # Parse command
            if len(data) == 0:
                continue
            
            cmd = data[0]
            
            # Handle commands based on current state
            if job_state == "waiting":
                # Handle main daemon commands
                if cmd == 0x01:  # Print any waiting jobs
                    queue = data[1:-1].decode('ascii', errors='ignore')
                    logging.info(f"\nLPR: Print waiting jobs for queue '{queue}'.")
                    # List jobs in the queue and log them
                    if hasattr(lpr_server, 'list_jobs_in_queue'):
                        jobs_listing = lpr_server.list_jobs_in_queue(queue)
                        logging.info(f"\n{jobs_listing}")
                    # Send positive acknowledgment
                    response = b'\x00'
                    trace_data(response, "server → client", port, trace, "ACK: Print command accepted")
                    writer.write(response)
                    await writer.drain()
                    
                elif cmd == 0x02:  # Receive a printer job
                    queue = data[1:-1].decode('ascii', errors='ignore')
                    logging.info(f"\nLPR: Receive printer job for queue '{queue}'.")
                    current_job = LPRJob(
                        job_id=lpr_server.get_next_job_id(),
                        user="unknown",
                        host="unknown",
                        queue=queue
                    )
                    job_state = "receiving_job_initial"
                    # Send positive acknowledgment
                    response = b'\x00'
                    trace_data(response, "server → client", port, trace, "ACK: Ready to receive job")
                    writer.write(response)
                    await writer.drain()
                    
                elif cmd == 0x03:  # Send queue state (short)
                    parts = data[1:-1].decode('ascii', errors='ignore').split()
                    queue = parts[0] if parts else "default"
                    logging.info(f"\nLPR: Send queue state (short) for queue '{queue}'.")
                    status = lpr_server.format_queue_status(queue, long_format=False)
                    response = status.encode('ascii')
                    trace_data(response, "server → client", port, trace, "Queue status (short)")
                    writer.write(response)
                    await writer.drain()
                    writer.close()
                    await writer.wait_closed()
                    return
                    
                elif cmd == 0x04:  # Send queue state (long)
                    parts = data[1:-1].decode('ascii', errors='ignore').split()
                    queue = parts[0] if parts else "default"
                    logging.info(f"\nLPR: Send queue state (long) for queue '{queue}'.")
                    status = lpr_server.format_queue_status(queue, long_format=True)
                    response = status.encode('ascii')
                    trace_data(response, "server → client", port, trace, "Queue status (long)")
                    writer.write(response)
                    await writer.drain()
                    writer.close()
                    await writer.wait_closed()
                    return
                    
                elif cmd == 0x05:  # Remove jobs
                    parts = data[1:-1].decode('ascii', errors='ignore').split()
                    queue = parts[0] if parts else "default"
                    agent = parts[1] if len(parts) > 1 else "unknown"
                    logging.info(f"\nLPR: Remove jobs from queue '{queue}' by agent '{agent}'.")
                    # Remove jobs in the specified queue
                    queue_dir = lpr_server.save_dir / queue
                    removed = 0
                    if queue_dir.exists() and queue_dir.is_dir():
                        job_dirs = [d for d in queue_dir.iterdir() if d.is_dir()]
                        for job_dir in job_dirs:
                            try:
                                # Optionally, filter by agent/user if needed
                                # Remove the job directory and all its contents
                                for f in job_dir.glob("*"):
                                    try:
                                        f.unlink()
                                    except Exception:
                                        pass
                                job_dir.rmdir()
                                removed += 1
                            except Exception as e:
                                logging.warning(f"Failed to remove job {job_dir}: {e}")
                    logging.info(f"Removed {removed} job(s) from queue '{queue}'.")
                    response = b'\x00'
                    trace_data(response, "server → client", port, trace, f"ACK: Removed {removed} job(s) from queue '{queue}'")
                    writer.write(response)
                    await writer.drain()
                    
                """
                else:
                    logging.info(f"\nLPR: Unknown main command 0x{cmd:02x}.")
                    # Send negative acknowledgment
                    response = b'\x01'
                    trace_data(response, "server → client", port, trace, "NAK: Unknown main command")
                    writer.write(response)
                    await writer.drain()
                """
                    
            elif job_state == "receiving_job_initial" and current_job:
                # After initial receive job command, expect subcommands only
                if cmd == 0x01 and len(data) == 2 and data[1] == 0x0A:
                    # Abort job
                    logging.info(f"\nLPR: Abort job {current_job.job_id}.")
                    current_job = None
                    job_state = "waiting"
                    response = b'\x00'
                    trace_data(response, "server → client", port, trace, "ACK: Job aborted")
                    writer.write(response)
                    await writer.drain()
                    
                elif cmd == 0x02:  # Receive control file
                    # Parse: 02 <count> SP <name> LF
                    try:
                        line = data.decode('ascii', errors='ignore').strip()
                        parts = line[1:].split(' ', 1)
                        if len(parts) >= 2:
                            count = int(parts[0])
                            filename = parts[1]
                            current_job.control_filename = filename
                            
                            # Parse control filename according to RFC 1179
                            job_number, hostname = lpr_server.parse_control_filename(filename)
                            if job_number and hostname:
                                current_job.job_number = job_number
                                current_job.hostname = hostname
                                logging.info(f"\nLPR: Receive control file '{filename}' ({count} bytes).")
                                logging.info(f"      Parsed - Job number: {job_number}, Hostname: {hostname}")
                            else:
                                logging.info(f"\nLPR: Receive control file '{filename}' ({count} bytes) - Invalid filename format!")
                            
                            # Send acknowledgment
                            response = b'\x00'
                            trace_data(response, "server → client", port, trace, "ACK: Ready for control file")
                            writer.write(response)
                            await writer.drain()
                            
                            # Read control file data
                            control_data = await reader.read(count)
                            
                            # Validate received data length
                            if len(control_data) != count:
                                logging.warning(
                                    f"\nLPR: ERROR - Expected {count} bytes, received {len(control_data)} bytes"
                                )
                                response = b'\x01'
                                trace_data(response, "server → client", port, trace, "NAK: Invalid control file length")
                                writer.write(response)
                                await writer.drain()
                                continue
                            
                            current_job.control_file = control_data
                            
                            # Parse control file with enhanced parsing
                            logging.info(f"\nLPR: Analysis of the control file:")
                            control_info = lpr_server.parse_control_file(control_data, log=True)

                            # Extract user and host from control file
                            parsed_commands = control_info.get('parsed_commands', {})
                            if 'H' in parsed_commands:
                                current_job.host = parsed_commands['H'][0]
                            if 'P' in parsed_commands:
                                current_job.user = parsed_commands['P'][0]
                            if 'J' in parsed_commands:
                                current_job.job_id = parsed_commands['J'][0]
                            if 'N' in parsed_commands:
                                current_job.file_name = parsed_commands['N'][0]
                            
                            trace_data(control_data, "client → server", port, trace, "Dump of the Control file")
                            
                            # Read terminating null byte
                            null_byte = await reader.read(1)
                            if null_byte == b'\x00':
                                # Send final acknowledgment
                                response = b'\x00'
                                trace_data(response, "server → client", port, trace, "ACK: Control file received")
                                writer.write(response)
                                await writer.drain()
                                job_state = "receiving_job"  # Move to next state
                            else:
                                logging.warning(f"\nLPR: ERROR - Expected null terminator, got: {null_byte}")
                                response = b'\x01'
                                trace_data(response, "server → client", port, trace, "NAK: Missing null terminator")
                                writer.write(response)
                                await writer.drain()
                        else:
                            logging.warning(f"\nLPR: Malformed control file command.")
                            response = b'\x01'
                            trace_data(response, "server → client", port, trace, "NAK: Malformed control file command")
                            writer.write(response)
                            await writer.drain()
                    except (ValueError, UnicodeDecodeError) as e:
                        logging.warning(f"\nLPR: Error parsing control file command: {e}")
                        response = b'\x01'
                        trace_data(response, "server → client", port, trace, "NAK: Control file parse error")
                        writer.write(response)
                        await writer.drain()
                    
                elif cmd == 0x03:  # Receive data file
                    # Parse: 03 <count> SP <name> LF
                    try:
                        line = data.decode('ascii', errors='ignore').strip()
                        parts = line[1:].split(' ', 1)
                        if len(parts) >= 2:
                            count = int(parts[0])
                            filename = parts[1]
                            current_job.data_filename = filename
                            
                            # Parse data filename according to RFC 1179
                            job_number, hostname = lpr_server.parse_data_filename(filename)
                            if job_number and hostname:
                                logging.info(f"\nLPR: Receive data file '{filename}' ({count} bytes).")
                                logging.info(f"      Parsed - Job number: {job_number}, Hostname: {hostname}")
                            else:
                                logging.info(f"\nLPR: Receive data file '{filename}' ({count} bytes) - Invalid filename format!")
                            
                            # Send acknowledgment
                            response = b'\x00'
                            trace_data(response, "server → client", port, trace, "ACK: Ready for data file")
                            writer.write(response)
                            await writer.drain()
                            
                            # Read data file
                            if count > 0:
                                data_content = await reader.read(count)
                                
                                # Validate received data length
                                if len(data_content) != count:
                                    logging.warning(f"\nLPR: ERROR - Expected {count} bytes, received {len(data_content)} bytes")
                                    response = b'\x01'
                                    trace_data(response, "server → client", port, trace, "NAK: Invalid data file length")
                                    writer.write(response)
                                    await writer.drain()
                                    continue
                                
                                current_job.data_file = data_content
                                trace_data(data_content, "client → server", port, trace, "Dump of the Data file")
                                
                                # Read terminating null byte
                                null_byte = await reader.read(1)
                                if null_byte == b'\x00':
                                    # Send final acknowledgment
                                    response = b'\x00'
                                    trace_data(response, "server → client", port, trace, "ACK: Data file received")
                                    writer.write(response)
                                    await writer.drain()
                                    job_state = "receiving_job"  # Move to next state
                                else:
                                    logging.warning(f"\nLPR: ERROR - Expected null terminator, got: {null_byte}")
                                    response = b'\x01'
                                    trace_data(response, "server → client", port, trace, "NAK: Missing null terminator")
                                    writer.write(response)
                                    await writer.drain()
                            
                        else:
                            logging.warning(f"\nLPR: Malformed data file command.")
                            response = b'\x01'
                            trace_data(response, "server → client", port, trace, "NAK: Malformed data file command")
                            writer.write(response)
                            await writer.drain()
                    except (ValueError, UnicodeDecodeError) as e:
                        logging.warning(f"\nLPR: Error parsing data file command: {e}")
                        response = b'\x01'
                        trace_data(response, "server → client", port, trace, "NAK: Data file parse error")
                        writer.write(response)
                        await writer.drain()
                        
                else:
                    logging.warning(f"\nLPR: Unknown subcommand 0x{cmd:02x} in initial job state.")
                    # Send negative acknowledgment
                    response = b'\x01'
                    trace_data(response, "server → client", port, trace, "NAK: Unknown subcommand")
                    writer.write(response)
                    await writer.drain()
                    
            elif job_state == "receiving_job" and current_job:
                # After receiving control file, expect data file if not already received
                if cmd == 0x03:  # Receive data file (if not already received)
                    # Parse: 03 <count> SP <name> LF
                    try:
                        line = data.decode('ascii', errors='ignore').strip()
                        parts = line[1:].split(' ', 1)
                        if len(parts) >= 2:
                            count = int(parts[0])
                            filename = parts[1]
                            current_job.data_filename = filename
                            
                            # Parse data filename according to RFC 1179
                            job_number, hostname = lpr_server.parse_data_filename(filename)
                            if job_number and hostname:
                                logging.info(f"\nLPR: Receive data file '{filename}' ({count} bytes).")
                                logging.info(f"      Parsed - Job number: {job_number}, Hostname: {hostname}")
                            else:
                                logging.info(f"\nLPR: Receive data file '{filename}' ({count} bytes) - Invalid filename format!")
                            
                            # Send acknowledgment
                            response = b'\x00'
                            trace_data(response, "server → client", port, trace, "ACK: Ready for data file")
                            writer.write(response)
                            await writer.drain()
                            
                            # Read data file
                            if count > 0:
                                data_content = await reader.read(count)
                                
                                # Validate received data length
                                if len(data_content) != count:
                                    logging.warning(f"\nLPR: ERROR - Expected {count} bytes, received {len(data_content)} bytes")
                                    #response = b'\x01'
                                    trace_data(response, "server → client", port, trace, "Invalid data file length")
                                    #writer.write(response)
                                    #await writer.drain()
                                    #continue
                                
                                current_job.data_file = data_content
                                trace_data(data_content, "client → server", port, trace, "Dump of the Data file")
                                
                                # Read terminating null byte
                                null_byte = await reader.read(1)
                                if null_byte == b'\x00':
                                    # Send final acknowledgment
                                    response = b'\x00'
                                    trace_data(response, "server → client", port, trace, "ACK: Data file received")
                                    writer.write(response)
                                    await writer.drain()
                                    
                                    # Job complete, save it
                                    if current_job.control_file and current_job.data_file:
                                        current_job.status = "completed"
                                        lpr_server.jobs[current_job.job_id] = current_job
                                        if lpr_server.save_files:
                                            lpr_server.save_job_files(current_job)
                                            logging.info(f"\nLPR: Job {current_job.job_id} completed and saved.")
                                        else:
                                            logging.info(f"\nLPR: Job {current_job.job_id} completed.")
                                        current_job = None
                                        job_state = "waiting"
                                else:
                                    if null_byte == b'':
                                        logging.warning(f"\nLPR: ERROR - Missing null terminator")
                                    else:
                                        logging.warning(f"\nLPR: ERROR - Expected null terminator, got: {null_byte}")
                                        response = b'\x01'
                                        trace_data(response, "server → client", port, trace, "NAK: Missing null terminator")
                                        writer.write(response)
                                        await writer.drain()
                            
                        else:
                            logging.warning(f"\nLPR: Malformed data file command.")
                            response = b'\x01'
                            trace_data(response, "server → client", port, trace, "NAK: Malformed data file command")
                            writer.write(response)
                            await writer.drain()
                    except (ValueError, UnicodeDecodeError) as e:
                        logging.warning(f"\nLPR: Error parsing data file command: {e}")
                        response = b'\x01'
                        trace_data(response, "server → client", port, trace, "NAK: Data file parse error")
                        writer.write(response)
                        await writer.drain()
                    
                else:
                    logging.warning(f"\nLPR: Unexpected command 0x{cmd:02x} in job reception state.")
                    # Send negative acknowledgment
                    response = b'\x01'
                    trace_data(response, "server → client", port, trace, "NAK: Unexpected command")
                    writer.write(response)
                    await writer.drain()
                    
            else:
                logging.warning(f"\nLPR: Unexpected command 0x{cmd:02x} in state {job_state}.")
                # Send negative acknowledgment
                response = b'\x01'
                trace_data(response, "server → client", port, trace, "NAK: Unexpected command")
                writer.write(response)
                await writer.drain()
                
    except Exception as e:
        logging.warning(f"LPR protocol error: {e}")
        # Send negative acknowledgment on error
        try:
            response = b'\x01'
            trace_data(response, "server → client", port, trace, "NAK: Protocol error")
            writer.write(response)
            await writer.drain()
        except:
            pass
    finally:
        if not writer.is_closing():
            writer.close()
            await writer.wait_closed()

async def handle_tcp(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    mapping: PortMapping,
    trace: bool,
    lpr_server: LprServer
) -> None:
    lp, rh, rp = mapping.port, mapping.remote_host, mapping.remote_port

    if rh is None or rp is None:  # local loopback without forwarding data to the printer
        if lp == 515:  # LPR port
            await handle_lpr_protocol(reader, writer, lp, trace, lpr_server)
        else:
            # Loopback behavior for other ports including 9100
            try:
                while True:
                    try:
                        data = await reader.read(4096)
                    except (ConnectionAbortedError, ConnectionResetError) as e:
                        logging.info(f"Client disconnected on port {lp}: {e}")
                        break
                    if not data:
                        break
                    # Apply replacements if configured
                    for old, new in REPLACEMENTS.get(lp, []):
                        data = data.replace(old, new)
                    trace_data(data, "local → local", lp, trace)
                    """
                    # Echo back
                    try:
                        writer.write(data)
                        await writer.drain()
                    except (ConnectionResetError, ConnectionAbortedError):
                        logging.warning(f"Write failed: client disconnected on port {lp}")
                        break
                    """
            except Exception as e:
                logging.warning(f"Local loopback error on port {lp}: {e}")
            finally:
                if not writer.is_closing():
                    writer.close()
                    await writer.wait_closed()
        return

    # otherwise, real TCP forward to the printer
    remote_reader = None
    remote_writer = None
    try:
        remote_reader, remote_writer = await asyncio.open_connection(rh, rp)
    except Exception as e:
        logging.warning(f"TCP connect {rh}:{rp} failed: {e}")
        writer.close()
        await writer.wait_closed()
        return

    async def pump(src_reader, dst_writer, direction):
        try:
            while True:
                data = await src_reader.read(4096)
                if not data:
                    break
                if direction == 'to_remote' and lp in REPLACEMENTS:
                    for old, new in REPLACEMENTS[lp]:
                        data = data.replace(old, new)
                tag = "client → remote" if direction == 'to_remote' else "remote → client"
                trace_data(data, tag, lp, trace)
                dst_writer.write(data)
                await dst_writer.drain()
        except Exception as e:
            logging.warning(f"Pump error ({direction}) on port {lp}: {e}")
        finally:
            dst_writer.close()

    try:
        await asyncio.gather(
            pump(reader, remote_writer, 'to_remote'),
            pump(remote_reader, writer, 'to_local'),
            return_exceptions=True
        )
    finally:
        # Ensure all connections are properly closed
        for w in [writer, remote_writer]:
            if w and not w.is_closing():
                w.close()
                try:
                    await w.wait_closed()
                except Exception:
                    pass

async def start_tcp(mapping: PortMapping, trace: bool, lpr_server: LprServer) -> None:
    try:
        server = await asyncio.start_server(
            lambda r, w: handle_tcp(r, w, mapping, trace, lpr_server),
            host='0.0.0.0', port=mapping.port
        )
    except PermissionError:
        logging.warning(f"Permission denied binding TCP port {mapping.port}")
        return

    if mapping.remote_host and mapping.remote_port:
        logging.info(f"TCP {mapping.port} → {mapping.remote_host}:{mapping.remote_port}")
    else:
        if mapping.port == 515:
            logging.info(f"TCP {mapping.port} LPR server (local-only)")
        else:
            logging.info(f"TCP {mapping.port} local-only")

    async with server:
        await server.serve_forever()

class UDPProxy(asyncio.DatagramProtocol):
    def __init__(self, mapping: PortMapping, trace: bool):
        self.mapping = mapping
        self.trace = trace
        self.client_addr: Optional[Tuple[str, int]] = None

    def connection_made(self, transport):
        self.transport = transport
        rh, rp = self.mapping.remote_host, self.mapping.remote_port
        logging.info(f"UDP {self.mapping.port} → {rh}:{rp}")

    def datagram_received(self, data: bytes, addr):
        if addr == (self.mapping.remote_host, self.mapping.remote_port):
            direction, target = 'printer → client', self.client_addr
        else:
            direction, self.client_addr = 'client → printer', addr
            target = (self.mapping.remote_host, self.mapping.remote_port)
        if not target:
            return
        trace_data(data, direction, self.mapping.port, self.trace)
        self.transport.sendto(data, target)

    def error_received(self, exc):
        logging.warning(f"UDP port {self.mapping.port} error: {exc}")

async def start_udp(mapping: PortMapping, trace: bool) -> None:
    loop = asyncio.get_event_loop()
    try:
        await loop.create_datagram_endpoint(
            lambda: UDPProxy(mapping, trace),
            local_addr=('0.0.0.0', mapping.port)
        )
    except PermissionError:
        logging.warning(f"Permission denied binding UDP port {mapping.port}")
    except OSError as e:
        logging.warning(f"UDP port {mapping.port} bind failed: {e}")


async def server_proxy(
    tcp_ports: List[PortMapping],
    udp_ports: List[PortMapping],
    trace: bool,
    save_files: bool,
    save_path: None,
) -> None:
    # Create LPR server instance
    lpr_server = LprServer(save_files=save_files, save_path=save_path)
    
    tasks = (
        [start_tcp(m, trace, lpr_server) for m in tcp_ports]
        + [start_udp(m, trace) for m in udp_ports]
    )
    await asyncio.gather(*tasks)


def main():
    parser = argparse.ArgumentParser(
        prog="pylpr server",
        description='RAW and LPR print server with proxy, forward and local loopback features.',
        epilog='RAW/LPR print server.',
    )
    parser.add_argument(
        '-a',
        '--address',
        help='Printer IP address (if not provided, runs in local-only mode)'
    )
    parser.add_argument(
        '-t',
        '--trace',
        action='store_true',
        help='Enable hex dump tracing'
    )
    parser.add_argument(
        '-s',
        '--save-files',
        action='store_true',
        default=False,
        help='Save received print jobs to disk'
    )
    parser.add_argument(
        '-p',
        '--save-path',
        type=str,
        default=None,
        help='Path name of the directory including the saved jobs (defaut: "lpr_jobs")'
    )
    parser.add_argument(
        '-q',
        '--quiet',
        action='store_true',
        default=False,
        help='Do not print debug data'
    )
    parser.add_argument(
        '-l',
        '--loopback',
        metavar='PORTS',
        help='Comma-separated list of ports to loopback instead of forwarding to the target IP (e.g., "515,9100")'
    )
    parser.add_argument(
        '-e',
        '--exclude',
        metavar='PORTS',
        help='Comma-separated list of ports to exclude from tracing (e.g., "515,9100")'
    )
    args = parser.parse_args()

    logging_level = logging.INFO
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

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    loopback = []
    if args.loopback:
        try:
            loopback = [int(p.strip()) for p in args.loopback.split(',')]
        except ValueError:
            logging.error("Invalid port number in loopback-ports")
            sys.exit(1)

    if args.exclude:
        try:
            ports = [int(p.strip()) for p in args.exclude.split(',')]
            EXCLUDED_TRACE_PORTS.update(ports)
        except ValueError:
            logging.error("Invalid port number in exclude-trace-ports")
            sys.exit(1)

    target_ip = args.address
    
    tcp_port_map = {
        515: 515,     # LPR
        9100: 9100,   # Raw
        631: 631,     # IPP
        5080: 80,     # HTTP (custom source to standard target)
        5443: 443,    # HTTPS (custom source to standard target)
    }

    def create_tcp_port_mapping(src, dst):
        return PortMapping(src, None, None) if src in loopback else PortMapping(src, target_ip, dst)

    # TCP ports
    tcp_ports = [
        create_tcp_port_mapping(src, dst) if target_ip else PortMapping(src, None, None)
        for src, dst in tcp_port_map.items()
    ]

    # UDP ports
    udp_port_list = [161, 3289, 5353]  # SNMP, Epson discovery, mDNS
    udp_ports = [
        PortMapping(port, target_ip, port) if target_ip else PortMapping(port, None, None)
        for port in udp_port_list
    ]
    
    if platform.system() == 'Windows':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    logging.info(f"Starting {'proxy' if target_ip else 'local-only'} mode...")
    logging.info(f"File saving: {'enabled' if args.save_files else 'disabled'}")
    if EXCLUDED_TRACE_PORTS:
        logging.info(f"Excluded ports from tracing: {sorted(EXCLUDED_TRACE_PORTS)}")

    try:
        asyncio.run(
            server_proxy(
                tcp_ports,
                udp_ports,
                args.trace,
                args.save_files,
                save_path=args.save_path
            )
        )
    except KeyboardInterrupt:
        logging.error('Terminated')

if __name__ == "__main__":
    main()
