# coding: UTF-8

import serial
import time
import os

class Usb_rs:

    def __init__(self, gui=False):
        self.ser = serial
        self.gui = gui
        self.read_chunk_timeout = 0.1
        self.last_error = ""
        self.port_name = None

    def _report_error(self, title, error):
        # Keep serial helper UI-framework agnostic for desktop and headless deployments.
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {title}: {error}")
    
    # Check if port is still open and valid
    def is_port_open(self):
        """Verify that serial port is still connected and responsive."""
        try:
            if self.ser is None or not isinstance(self.ser, serial.Serial):
                return False
            # On Linux, verify device file still exists
            if hasattr(self, 'port_name') and self.port_name:
                if not os.path.exists(self.port_name):
                    self._report_error("Port State Check", f"Device {self.port_name} no longer exists")
                    return False
            # Check if port is actually open
            if not self.ser.is_open:
                self._report_error("Port State Check", "Port is closed")
                return False
            return True
        except Exception as e:
            self._report_error("Port State Check", f"Error checking port: {e}")
            return False
    
    # Open port
    def open(self, port, speed):
        ret = False

        try:
            # Use longer timeout for batch reading
            self.ser = serial.Serial(port, speed, timeout=self.read_chunk_timeout)
            self.port_name = port
            self.last_error = ""
            self._report_error("Port Open", f"Successfully opened {port} at {speed} baud")
            ret = True
        except Exception as e:
            self.last_error = str(e)
            self._report_error("Port Open Error", f"{e}")
        
        return ret

    # Close port
    def close(self):
        ret = False

        try:   
            if self.ser and isinstance(self.ser, serial.Serial):
                self.ser.close()
                self._report_error("Port Close", f"Successfully closed {self.port_name}")
            ret = True
        except Exception as e:
            self._report_error("Port Close Error", f"{e}")
        
        return ret

    # Send command
    def sendMsg(self, strMsg):
        ret = False

        try:
            strMsg = strMsg + '\r\n'                #Add a terminator, CR+LF, to transmitted command
            self.ser.write(bytes(strMsg, 'utf-8'))  #Convert to byte type and send
            self.last_error = ""
            ret = True
        except Exception as e:
            self.last_error = str(e)
            self._report_error("Send Error", f"{e}")

        return ret
    
    # Receive - Optimized with batch reading instead of byte-by-byte
    def receiveMsg(self, timeout):
        """Receive message optimized for batch reads. Reads up to 64 bytes per iteration."""
        msgBuf = b""
        try:
            start = time.time()
            while True:
                # Try to read up to 64 bytes at once (batch reading)
                rcv = self.ser.read(64)
                if rcv:
                    msgBuf += rcv
                    # Check if message is complete (ends with LF)
                    if b"\n" in msgBuf:
                        # Split on newline, process first message
                        lines = msgBuf.split(b"\n", 1)
                        # Remove carriage returns and decode
                        msg_str = lines[0].replace(b"\r", b"").decode('utf-8', errors='ignore')
                        self.last_error = ""
                        return msg_str
                else:
                    # No data available in this read cycle
                    pass
                
                # Timeout processing
                if time.time() - start > timeout:
                    if msgBuf:
                        # Partial data received - return what we have
                        msg_str = msgBuf.replace(b"\r", b"").decode('utf-8', errors='ignore').strip()
                        if msg_str:
                            self._report_error("Receive Partial", f"Got partial response after {timeout}s: {msg_str[:50]}")
                            return msg_str
                    self._report_error("Receive Timeout", f"No complete response after {timeout}s (received {len(msgBuf)} bytes)")
                    return "Timeout Error"
        except Exception as e:
            self.last_error = str(e)
            self._report_error("Receive Error", f"{e}")
            return f"Error: {e}"
    
    # Transmit and receive commands
    def SendQueryMsg(self, strMsg, timeout):
        ret = Usb_rs.sendMsg(self, strMsg)
        if ret:
            msgBuf_str = Usb_rs.receiveMsg(self, timeout)   #Receive response when command transmission is succeeded
        else:
            detail = self.last_error if self.last_error else "Send failed"
            msgBuf_str = f"Error: {detail}"

        return msgBuf_str



