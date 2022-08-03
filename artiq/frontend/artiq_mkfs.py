#!/usr/bin/env python3

import argparse
import struct
from artiq.frontend.artiq_cntn import Cntn
from argparse import RawTextHelpFormatter


def get_argparser():
    parser = argparse.ArgumentParser(description="ARTIQ flash storage image generator",
                                     formatter_class=argparse.RawTextHelpFormatter)

    parser.add_argument("output", help="output file")

    parser.add_argument("-s", nargs=2, action="append", default=[],
                        metavar=("KEY", "STRING"),
                        help="add string")
    parser.add_argument("-f", nargs=2, action="append", default=[],
                        metavar=("KEY", "FILENAME"),
                        help="add file contents")
    parser.add_argument("-e", action="append", default=[],
                        metavar=("ACTION"), choices=["write_ch_names"],
                        help="extended actions:\n"
                        "------------------\n"
                        "write_ch_names\n"
                        "    store channel numbers and corresponding device\n"
                        "    names from channel names file to core device config\n"
                        "    change location of the file by -c")
    parser.add_argument("-c", "--channel-names", default="channel_ntn.txt",
                        help="channel names file (default: '%(default)s')")

    return parser


def write_record(f, key, value):
    key_size = len(key) + 1
    value_size = len(value)
    record_size = key_size + value_size + 4
    f.write(struct.pack(">l", record_size))
    f.write(key.encode())
    f.write(b"\x00")
    f.write(value)


def write_end_marker(f):
    f.write(b"\xff\xff\xff\xff")


def main():
    args = get_argparser().parse_args()
    with open(args.output, "wb") as fo:
        for key, string in args.s:
            write_record(fo, key, string.encode())
        for key, filename in args.f:
            with open(filename, "rb") as fi:
                write_record(fo, key, fi.read())
        for action in args.e:
            if action == "write_ch_names":
                cntn = Cntn(args.channel_names).get_config_string()
                write_record(fo, "channel_names", cntn.encode())
        write_end_marker(fo)

if __name__ == "__main__":
    main()
