"""Tri-color variant of the Waveshare 2.13" V4 panel driver."""

import logging

from . import epdconfig

EPD_WIDTH = 122
EPD_HEIGHT = 250

logger = logging.getLogger(__name__)


class EPD:
    def __init__(self) -> None:
        self.reset_pin = epdconfig.RST_PIN
        self.dc_pin = epdconfig.DC_PIN
        self.busy_pin = epdconfig.BUSY_PIN
        self.cs_pin = epdconfig.CS_PIN
        self.width = EPD_WIDTH
        self.height = EPD_HEIGHT

    def reset(self) -> None:
        epdconfig.digital_write(self.reset_pin, 1)
        epdconfig.delay_ms(20)
        epdconfig.digital_write(self.reset_pin, 0)
        epdconfig.delay_ms(2)
        epdconfig.digital_write(self.reset_pin, 1)
        epdconfig.delay_ms(20)

    def send_command(self, command: int) -> None:
        epdconfig.digital_write(self.dc_pin, 0)
        epdconfig.digital_write(self.cs_pin, 0)
        epdconfig.spi_writebyte([command])
        epdconfig.digital_write(self.cs_pin, 1)

    def send_data(self, data: int) -> None:
        epdconfig.digital_write(self.dc_pin, 1)
        epdconfig.digital_write(self.cs_pin, 0)
        epdconfig.spi_writebyte([data])
        epdconfig.digital_write(self.cs_pin, 1)

    def send_data2(self, data) -> None:
        epdconfig.digital_write(self.dc_pin, 1)
        epdconfig.digital_write(self.cs_pin, 0)
        epdconfig.spi_writebyte2(data)
        epdconfig.digital_write(self.cs_pin, 1)

    def busy(self) -> None:
        logger.debug("e-Paper busy")
        while epdconfig.digital_read(self.busy_pin) != 0:
            epdconfig.delay_ms(10)
        logger.debug("e-Paper busy release")

    def set_windows(self, xstart: int, ystart: int, xend: int, yend: int) -> None:
        self.send_command(0x44)
        self.send_data((xstart >> 3) & 0xFF)
        self.send_data((xend >> 3) & 0xFF)

        self.send_command(0x45)
        self.send_data(ystart & 0xFF)
        self.send_data((ystart >> 8) & 0xFF)
        self.send_data(yend & 0xFF)
        self.send_data((yend >> 8) & 0xFF)

    def set_cursor(self, xstart: int, ystart: int) -> None:
        self.send_command(0x4E)
        self.send_data(xstart & 0xFF)

        self.send_command(0x4F)
        self.send_data(ystart & 0xFF)
        self.send_data((ystart >> 8) & 0xFF)

    def init(self) -> int:
        if epdconfig.module_init() != 0:
            return -1

        self.reset()

        self.busy()
        self.send_command(0x12)
        self.busy()

        self.send_command(0x01)
        self.send_data(0xF9)
        self.send_data(0x00)
        self.send_data(0x00)

        self.send_command(0x11)
        self.send_data(0x03)

        self.set_windows(0, 0, self.width - 1, self.height - 1)
        self.set_cursor(0, 0)

        self.send_command(0x3C)
        self.send_data(0x05)

        self.send_command(0x18)
        self.send_data(0x80)

        self.send_command(0x21)
        self.send_data(0x80)
        self.send_data(0x80)

        self.busy()
        return 0

    def ondisplay(self) -> None:
        self.send_command(0x20)
        self.busy()

    def getbuffer(self, image):
        img = image
        imwidth, imheight = img.size
        if imwidth == self.width and imheight == self.height:
            img = img.convert("1")
        elif imwidth == self.height and imheight == self.width:
            img = img.rotate(90, expand=True).convert("1")
        else:
            logger.warning("Wrong image dimensions: must be %sx%s", self.width, self.height)
            return [0x00] * (((self.width + 7) // 8) * self.height)

        buf = bytearray(img.tobytes("raw"))
        return buf

    def display(self, imageblack, imagered) -> None:
        self.send_command(0x24)
        self.send_data2(imageblack)

        self.send_command(0x26)
        self.send_data2(imagered)

        self.ondisplay()

    def clear(self) -> None:
        linewidth = ((self.width + 7) // 8) * self.height
        buf = [0xFF] * linewidth

        self.send_command(0x24)
        self.send_data2(buf)

        self.send_command(0x26)
        self.send_data2(buf)

        self.ondisplay()

    def Clear(self) -> None:
        self.clear()

    def sleep(self) -> None:
        self.send_command(0x10)
        self.send_data(0x01)

        epdconfig.delay_ms(2000)
        epdconfig.module_exit()


