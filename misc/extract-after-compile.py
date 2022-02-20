# Derived from https://github.com/airbus-seclab/ilo4_toolbox

# This script works like the default ilo4_extract script, but
# it's been retooled to extract a working elf.bin from a final patched
# binary generated by ilo4_toolbox -- I used it to extract the final
# 273.bin.fancommands binary into something I could diff in Ida

#!/usr/bin/python

import os
import sys
import json
from ilo4 import *
from struct import unpack_from
from collections import OrderedDict


BEGIN_SIGN = "--=</Begin HP Signed File Fingerprint\>=--\n"
END_SIGN = "--=</End HP Signed File Fingerprint\>=--\n"
BEGIN_CERT = "-----BEGIN CERTIFICATE-----\n"
END_CERT = "-----END CERTIFICATE-----\n"

IMG_LIST = ["elf", "kernel_main", "kernel_recovery"]

HPIMAGE_HDR_SIZE = 0x4A0
BOOTLOADER_HDR_SIZE = 0x440
IMG_HDR_SIZE = 0x440


if len(sys.argv) != 3:
    print "usage: %s <filename> <outdir>"
    sys.exit(1)

filename = sys.argv[1]
outdir = sys.argv[2]

if not os.path.exists(outdir):
    os.makedirs(outdir)

with open(filename, "rb") as fff:
    data = fff.read()

offsets_map = OrderedDict()
global_offset = 0

# data starts at
# 01 00 00 00 29 32 EC AE CC 69 D8 43 BD 0E 61 DC 34 06 F7 1B 00 00 00 00
# have to add this to top of function
targetListsize = unpack_from("<L", data)[0]

print "\n[+] iLO target list: %x element(s)" % (targetListsize)

data = data[4:]
global_offset += 4

for i in range(targetListsize):
    raw = data[:0x10]
    dev = ""
    id = uuid.UUID(raw.encode("hex"))
    if id in TARGETS:
        dev = TARGETS[id]

    print "    target 0x%x (%s)" % (i, dev)
    print hexdump(raw)

    if dev == "":
        print "[x] unknown target"
        sys.exit(0)

    data = data[0x10:]
    global_offset += 0x10

data = data[4:]
global_offset += 4

print hexdump(data[0:100])
#------------------------------------------------------------------------------
# get signature: should be iLO3, iLO4 or iLO5

ilo_sign = data[:4]
ilo_bootloader_header = data[:BOOTLOADER_HDR_SIZE]
ilo_bootloader_footer = data[-0x40:]
print ilo_sign
data = data[BOOTLOADER_HDR_SIZE:]
offsets_map["BOOTLOADER_HDR"] = global_offset
global_offset += BOOTLOADER_HDR_SIZE

print "[+] iLO bootloader header : %s" % (ilo_bootloader_header[:0x1a])

with open(outdir + "/bootloader.hdr", "wb") as fff:
    fff.write(ilo_bootloader_header)

bootloader_header = BootloaderHeader.from_buffer_copy(ilo_bootloader_header)
bootloader_header.dump()

with open(outdir + "/bootloader.sig", "wb") as fff:
    fff.write(bootloader_header.to_str(bootloader_header.signature))


#------------------------------------------------------------------------------
# extract Bootloader footer and cryptographic parameters

print "[+] iLO Bootloader footer : %s" % (ilo_bootloader_footer[:0x1a])

bootloader_footer = BootloaderFooter.from_buffer_copy(ilo_bootloader_footer)
bootloader_footer.dump()

total_size = bootloader_header.total_size

print "\ntotal size:    0x%08x" % total_size
print "payload size:  0x%08x" % len(data)
print "kernel offset: 0x%08x\n" % bootloader_footer.kernel_offset

offsets_map["BOOTLOADER"] = global_offset + total_size - bootloader_footer.kernel_offset - BOOTLOADER_HDR_SIZE
ilo_bootloader = data[-bootloader_footer.kernel_offset:-BOOTLOADER_HDR_SIZE]

with open(outdir + "/bootloader.bin", "wb") as fff:
    fff.write(ilo_bootloader)

data = data[:total_size-BOOTLOADER_HDR_SIZE]
print hexdump(data[0:100])
ilo_crypto_params = data[len(data)-((~bootloader_footer.sig_offset + 1) & 0xFFFF): len(data)-0x40]

with open(outdir + "/sign_params.raw", "wb") as fff:
    fff.write(ilo_crypto_params)

crypto_params = SignatureParams.from_buffer_copy(ilo_crypto_params)
crypto_params.dump()


#------------------------------------------------------------------------------
# extract images

ilo_num = 0

off = data.find(ilo_sign)

while off >= 0:
    print hexdump(data[:400])
    # skip padding
    if data[:off] != "\xff" * off:
        with open(outdir + "/failed_assert.bin", "wb") as fff:
            fff.write(data)

    assert(data[:off] == "\xff" * off)
    data = data[off:]
    global_offset += off

    # extract header
    ilo_header = data[:IMG_HDR_SIZE]
    data = data[IMG_HDR_SIZE:]

    with open(outdir + "/%s.hdr" % IMG_LIST[ilo_num], "wb") as fff:
        fff.write(ilo_header)

    print "[+] iLO Header %d: %s" % (ilo_num, ilo_header[:0x1a])

    img_header = ImgHeader.from_buffer_copy(ilo_header)
    img_header.dump()

    with open(outdir + "/%s.sig" % IMG_LIST[ilo_num], "wb") as fff:
        fff.write(img_header.to_str(img_header.signature))

    payload_size = img_header.raw_size - IMG_HDR_SIZE

    data1 = data[:payload_size]
    data = data[payload_size:]

    # insert img into offsets map
    offsets_map["%s_HDR" % IMG_LIST[ilo_num].upper()] = global_offset
    global_offset += IMG_HDR_SIZE
    offsets_map["%s" % IMG_LIST[ilo_num].upper()] = global_offset
    global_offset += payload_size

    psz, = unpack_from("<L", data1)
    print hexdump(data1[0:100])
    print psz
    data1 = data1[4:]
    assert(psz == payload_size-4)
    assert(psz == len(data1))

    window = ['\0'] * 0x1000
    wchar = 0

    with open(outdir + "/%s.raw" % IMG_LIST[ilo_num], "wb") as fff:
        fff.write(data1)

    print "[+] Decompressing"

    output_size = decompress_all(data1, outdir + "/%s.bin" % IMG_LIST[ilo_num])
    print "    decompressed size : 0x%08x\n" % (output_size)

    print "[+] Extracted %s.bin" % IMG_LIST[ilo_num]

    off = data.find(ilo_sign)
    print off
    print hexdump(data[0:100])

    ilo_num += 1
    if ilo_num == 3:
        break


#------------------------------------------------------------------------------
# output offsets map

print "[+] Firmware offset map"
for part, offset in offsets_map.iteritems():
    print "  > %20s at 0x%08x" % (part, offset)

with open(outdir + "/firmware.map", "wb") as fff:
    fff.write(json.dumps(offsets_map, sort_keys=True, indent=4, separators=(',', ': ')))

print "\n> done\n"
