# LPR printer client/server with proxy support

RFC 1179 client and server toolkits and Python library for interacting with printers via LPR protocol or RAW mode, as well as a proxy/server for debugging, job capture, and protocol analysis.

## Features

- RFC 1179 LPR protocol implementation
- Easy-to-use Python API for client and server LPR features
- LPR (515) and RAW (9100) protocol support
- Print job simulation and debugging
- Bidirectional TCP/UDP proxy and traffic inspection
- Job queue management and archiving
- Hexdump tracing and protocol analysis
- Cross-platform (Windows, Linux, macOS)

## Installation

```bash
pip install PyLpr
```

## Command Line Usage

### Client
Run the client to send print jobs to a printer:

```bash
python3 -m PyLpr client -a <printer-ip> -f <file.txt> [-p PORT] [--simple] [--queue QUEUE] [--debug]
```

#### Client Options
- `-a`, `--address`: Printer host name or IP address (required)
- `-p`, `--port`: Printer port (default: 515 for LPR, 9100 for RAW)
- `-f`, `--file`: File to be printed (required)
- `-q`, `--queue`: Queue name (default: PASSTHRU)
- `-e`, `--epson`: Use Epson header and footer
- `-d`, `--debug`: Enable debug output
- `-r`, `--reserved`: Use reserved port range (721-731)

Notes:

- Use the `epson` flag to send Epson-specific headers/footers
- Use the `reserved` flag to comply with RFC 1179 reserved port requirements
- Debugging: Enable logging for protocol analysis

### Server/Proxy
Run the server/proxy to capture, forward, or analyze print jobs:
```bash
python3 -m PyLpr server -a <printer-ip> [-t] [-l LOOPBACK_PORTS] [--save-path PATH] [--quiet]
```

#### Server Options
- `-a`, `--address`: Target printer IP (for proxy mode)
- `-t`, `--trace`: Enable hexdump tracing
- `-l`, `--loopback`: Comma-separated list of ports to loopback
- `-e`, `--exclude`: Ports to exclude from tracing
- `--save-path`: Directory to save captured jobs
- `-q`, `--quiet`: Suppress debug output

## Python API Usage

### Client API
The client API allows sending print jobs, check queue status, and interact with printers programmatically.

#### Basic Usage

```python
from pylpr import LprClient

with LprClient('192.168.1.100', port="LPR", queue='PASSTHRU') as printer:
    # Send a print job from a file
    with open('document.txt', 'rb') as f:
        printer.send(f.read())
```

#### LprClient Class Reference
- `LprClient(hostname, port=515, queue='PASSTHRU', ...)` – Initialize client

    - **hostname** (`str`):  
      Printer hostname or IP address to connect to.

    - **port** (`int` or `str`, default: `9100`):  
      Port number for the printer. Use `9100` or `RAW` for RAW printing, `515` or `"LPR"` for LPR protocol.

    - **timeout** (`float`, default: `5.0`):  
      Socket timeout in seconds for network operations.

    - **queue** (`str`, default: `"PASSTHRU"`):  
      LPR queue name, used when sending jobs via LPR protocol.

    - **recv_buffer** (`int`, default: `4096`):  
      Size of the receive buffer for socket operations.

    - **username** (`str`, optional):  
      Username for LPR jobs. Defaults to the current system user if not specified.

    - **job_name** (`str`, optional):  
      Job name for LPR jobs. Defaults to `"LprClientJob"` if not specified.

    - **file_name** (`str`, optional):  
      File name for LPR jobs. Defaults to the job name if not specified.

    - **use_reserved_port** (`bool`, default: `False`):  
      If `True`, attempts to use a reserved source port (721-731) as required by RFC 1179 for LPR protocol.

- Methods:

    | **Method**              | **Description**                                                                                                                  |
    | ----------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
    | `connect()`             | Opens a TCP socket connection to the printer at the specified host and port, with timeout.                                       |
    | `disconnect()`          | Gracefully shuts down and closes the socket connection if open.                                                                  |
    | `send(data: bytes)`     | Sends `bytes` directly to the printer over the socket connection.                                                            |
    | `remote_cmd(cmd: str, args: bytes)` | Constructs a Remote Mode command: 2-byte ASCII command + 2-byte little-endian length + arguments.                                |
    | `set_timer`             | Constructs the "TI" remote command to synchronize RTC by setting the current time.                                               |

- Context manager support: `with LprClient(...) as printer:`

#### Epson Remote Mode commands

`LprClient` can be used to send *Epson Remote Mode commands* to the printer. Notice that the LPR or RAW channels do not support receiving payload responses from the Epson printer.

Comprehensive, unified documentation for Epson’s *Remote Mode commands* does not exist: support varies by model, and command references are scattered across service manuals, programming guides and third-party sources (for example, the [Developer's Guide to Gutenprint](https://gimp-print.sourceforge.io/reference-html/x952.html) or [GIMP-Print - ESC/P2 Remote Mode Commands](http://osr507doc.xinuos.com/en/OSAdminG/OSAdminG_gimp/manual-html/gimpprint_37.html)).

General sequences:

| **Command**           | **Description**                                              | **String**   |
| --------------------- | ------------------------------------------------------------ |------------- |
| `LF`                  | Line Feed (new line).                                        |              |
| `SP`                  | Space.                                                       |              |
| `NUL`                 | b'\x00'.                                                     |              |
| `FF`                  | Form Feed (b'\x0c'); flushes the buffer / ejects the page.   |              |
| `INITIALIZE_PRINTER`  | Resets printer to default state (ESC @).                     |              |

Specific Epson sequences:

| **Command**           | **Description**                                              | **String**   |
| --------------------- | ------------------------------------------------------------ |------------- |
| `EXIT_PACKET_MODE`    | Exit IEEE 1284.4 (D4) packet mode. See [1], 5.1.1 Exit Packet Mode. Must be sent before any other command. |              |
| `REMOTE_MODE`         | Enter Epson Remote Command mode. See [1], 6.1.1 Enter Remote Mode  | `(R`         |
| `ENTER_REMOTE_MODE`   | Initialize printer and enter Epson Remote Command mode.      |              |
| `EXIT_REMOTE_MODE`    | Exits Remote Mode. See [1], 6.1.14 Terminate Remote Mode |              |
| `JOB_START`           | Begins a print job. "JS", b'\x00\x00\x00\x00'. See [1], Start job “JS” nn 00H 00H <job name> m1. It is necessary to send the TI command before the JS command. | `JS`         |
| `JOB_END`             | Ends a print job. "JE", b'\x00'. See [1], End job “JE” 01H 00H 00H | `JE`         |
| `PRINT_NOZZLE_CHECK`  | Triggers a nozzle check print pattern. "NC", b'\x00\x00'     | `NC`         |
| (nozzle check)        | "NC", b'\x00\x10'                                            | `NC`         |
| `VERSION_INFORMATION` | Requests firmware or printer version info. "VI", b'\x00\x00' | `VI`         |
| `LD`                  | Load NVR Settings. See [1], Load Power-On Default NVR into RAM (Remote Mode) "LD" 00H 00H | `LD`         |
| (Run print-head cleaning) | "CH" 02H 00H 00H                                         | `CH`         |
| (Return the printer ID) | ESC 01 @EJL [sp] ID\r\n | `ID` |

[1]: handbook named "EPSON Programming Guide For 4 Color EPSON Ink Jet Printer XP-410 (Level I)", also including the description of following remote mode commands:

Description    | Command | Syntax
-------------- | -- | ----------------------------------
Set printer timer, synchronize RTC | TI | "TI" 08H 00H 00H YYYY MM DD hh mm ss
Set horizontal print position | FP | “FP” 03H 00H 00H m1 m2
Turn printer state reply on/off | ST | “ST” 02H 00H 00H m1
Set Job Name | JH | Job name set “JH” nL nH 00H m1 m2 m3 m4 m5 <job name>
Paper Feed Setup | SN | Set mechanism sequence "SN" 01H 00H 00H
Set Media information | MI | Select paper media “MI” 04H 00H 00H m1 m2 m3
Set double paper print | DP | Select Duplex Printing “DP” 02H 00H 00H m1
Set user setting | US | User Setting “US” 03H 00H 00H m1 m2
Select paper path | PP | Select paper path “PP” 03H 00H 00H m1 m2
Save Setting | SV | “SV” 00H 00H

See https://gimp-print.sourceforge.io/reference-html/x952.html for other ones.

The following code prints the nozzle-check print pattern:

```python
from pylpr import LprClient

with LprClient('192.168.1.100', port="LPR", queue='PASSTHRU') as lpr:
    data = (
        lpr.EXIT_PACKET_MODE +    # Exit packet mode
        lpr.ENTER_REMOTE_MODE +   # Engage remote mode commands
        lpr.PRINT_NOZZLE_CHECK +  # Issue nozzle-check print pattern
        lpr.EXIT_REMOTE_MODE +    # Disengage remote control
        lpr.JOB_END               # Mark maintenance job complete
    )
    lpr.send(data)
```

### Server/Proxy API
Capture, forward, manage LPR requests. Also process RAW requests.

#### Basic Usage
```python
from pylpr import LprServer

server = LprServer(save_files=True, save_path='lpr_jobs')
# The server can be integrated into an asyncio event loop for advanced use
# For simple usage, run via command line or module interface
```

#### LprServer Class Reference
- `LprServer(save_files=True, save_path=None)` – Initialize server

  - `save_files` (`bool`, default: `True`):
    If `True`, received print jobs (control and data files) are saved to disk. If `False`, jobs are discarded after dumping data.
  - `save_path` (`str`, default: `"lpr_jobs"`):
    if `save_files` is set to `True`, directory path where received print jobs are saved. If not specified, defaults to "lpr_jobs" in the current working directory.

- `get_next_job_id()` – Generate next job ID
- `parse_control_file(content: bytes)` – Parse LPR control file
- `format_queue_status(queue: str, long_format=False)` – Format queue status
- `save_job_files(job: LPRJob)` – Save job files to disk

## Examples

### Capture LPR job:
```bash
python3 -m PyLpr server -a 192.168.1.100 -t -l 515
```

### Send print job:
```python
from pylpr import LprClient

with LprClient('192.168.1.100', port=9100) as printer:
    printer.send(b'\x1B@Hello Printer!\x0C')
```

### Run as a module
```bash
python -m pylpr client -a 192.168.1.100 -f file.txt
python -m pylpr server -a 192.168.1.100 -t
```

## License

EUPL-1.2 License - See [LICENSE](LICENSE.txt) for details.
