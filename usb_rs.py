# coding: UTF-8

import serial
import time

class Usb_rs:

    def __init__(self, gui=False):
        self.ser = serial
        self.gui = gui
        self.read_chunk_timeout = 0.05
        self.last_error = ""

    def _report_error(self, title, error):
        # Keep serial helper UI-framework agnostic for desktop and headless deployments.
        print(f"{title}: {error}")
    
    #Open port
    def open(self, port, speed):
        ret = False

        try:
            # Use a short blocking timeout to avoid CPU-heavy busy loops.
            self.ser = serial.Serial(port, speed, timeout=self.read_chunk_timeout)
            self.last_error = ""
            ret = True
        except Exception as e:
            self.last_error = str(e)
            self._report_error("Open Error", e)
        
        return ret

    #Close port
    def close(self):
        ret = False

        try:   
            self.ser.close()
            ret = True
        except Exception as e:
            self._report_error("Close Error", e)
        
        return ret
    #Send command
    def sendMsg(self, strMsg):
        ret = False

        try:
            strMsg = strMsg + '\r\n'                #Add a terminator, CR+LF, to transmitted command
            self.ser.write(bytes(strMsg, 'utf-8'))  #Convert to byte type and send
            self.last_error = ""
            ret = True
        except Exception as e:
            self.last_error = str(e)
            self._report_error("Send Error", e)

        return ret
    
    #Receive
    def receiveMsg(self, timeout):

        msgBuf = bytes(range(0))                    #Received Data

        try:
            start = time.time()                     #Record time for timeout
            while True:
                rcv = self.ser.read(1)              #Blocking read with short timeout
                if rcv:
                    if rcv == b"\n":                #End the loop when LF is received
                        msgBuf = msgBuf.decode('utf-8')
                        break
                    elif rcv == b"\r":              #Ignore the terminator CR
                        pass
                    else:
                        msgBuf = msgBuf + rcv
                
                #Timeout processing
                if  time.time() - start > timeout:
                    msgBuf = "Timeout Error"
                    break
        except Exception as e:
            self.last_error = str(e)
            self._report_error("Receive Error", e)
            msgBuf = f"Error: {e}"

        return msgBuf
    
    #Transmit and receive commands
    def SendQueryMsg(self, strMsg, timeout):
        ret = Usb_rs.sendMsg(self, strMsg)
        if ret:
            msgBuf_str = Usb_rs.receiveMsg(self, timeout)   #Receive response when command transmission is succeeded
        else:
            detail = self.last_error if self.last_error else "Send failed"
            msgBuf_str = f"Error: {detail}"

        return msgBuf_str



