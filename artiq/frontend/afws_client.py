#!/usr/bin/env python3

import sys
import argparse
import os
import socket
import ssl
import io
import zipfile
import json
from prettytable import PrettyTable
from getpass import getpass
from tqdm import tqdm


def get_artiq_cert():
    try:
        import artiq
    except ImportError:
        return None
    filename = os.path.join(os.path.dirname(artiq.__file__), "afws.pem")
    if not os.path.isfile(filename):
        return None
    return filename


def get_artiq_rev():
    try:
        import artiq
    except ImportError:
        return None
    return artiq._version.get_rev()


def zip_unarchive(data, directory):
    buf = io.BytesIO(data)
    with zipfile.ZipFile(buf) as archive:
        archive.extractall(directory)


class Client:
    def __init__(self, server, port, cafile):
        self.ssl_context = ssl.create_default_context(cafile=cafile)
        self.raw_socket = socket.create_connection((server, port))
        try:
            self.socket = self.ssl_context.wrap_socket(self.raw_socket, server_hostname=server)
        except:
            self.raw_socket.close()
            raise
        self.fsocket = self.socket.makefile("rwb")

    def close(self):
        self.socket.close()
        self.raw_socket.close()

    def send_command(self, *command):
        self.fsocket.write((" ".join(command) + "\n").encode())
        self.fsocket.flush()

    def read_line(self):
        return self.fsocket.readline().decode("ascii")

    def read_reply(self):
        return self.fsocket.readline().decode("ascii").split()

    def read_json(self):
        return json.loads(self.fsocket.readline().decode("ascii"))

    def login(self, username, password):
        self.send_command("LOGIN", username, password)
        return self.read_reply() == ["HELLO"]

    def build(self, rev, variant, log):
        if not variant:
            variants = self.get_variants()
            if len(variants) != 1:
                raise ValueError("User can build more than 1 variant - need to specify")
            variant = variants[0][0]
        print("Building variant: {}".format(variant))
        if log:
            self.send_command("BUILD", rev, variant, "LOG_ENABLE")
        else:
            self.send_command("BUILD", rev, variant)
        reply = self.read_reply()[0]
        if reply != "BUILDING":
            return reply, None
        print("Build in progress. This may take 10-15 minutes.")
        if log:
            line = self.read_line()
            while line != "" and line.startswith("LOG"):
                print(line[4:], end="")
                line = self.read_line()
            reply, status = line.split()
        else:
            reply, status = self.read_reply()
        if reply != "DONE":
            raise ValueError("Unexpected server reply: expected 'DONE', got '{}'".format(reply))
        if status != "done":
            return status, None
        print("Build completed. Downloading...")
        reply, length = self.read_reply()
        if reply != "PRODUCT":
            raise ValueError("Unexpected server reply: expected 'PRODUCT', got '{}'".format(reply))
        length = int(length)
        contents = bytearray()
        with tqdm(total=length, unit="iB", unit_scale=True, unit_divisor=1024) as progress_bar:
            total = 0
            while total != length:
                chunk_len = min(4096, length-total)
                contents += self.fsocket.read(chunk_len)
                total += chunk_len
                progress_bar.update(chunk_len)
        print("Download completed.")
        return "OK", contents

    def passwd(self, password):
        self.send_command("PASSWD", password)
        return self.read_reply() == ["OK"]
    
    def get_variants(self):
        self.send_command("GET_VARIANTS")
        reply = self.read_reply()[0]
        if reply != "OK":
            raise ValueError("Unexpected server reply: expected 'OK', got '{}'".format(reply))
        return self.read_json()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="nixbld.m-labs.hk", help="server to connect to (default: %(default)s)")
    parser.add_argument("--port", default=7402, type=int, help="port to connect to (default: %(default)d)")
    parser.add_argument("--cert", default=None, help="SSL certificate file used to authenticate server (default: afws.pem in ARTIQ)")
    parser.add_argument("username", help="user name for logging into AFWS")
    action = parser.add_subparsers(dest="action")
    action.required = True
    act_build = action.add_parser("build", help="build and download firmware")
    act_build.add_argument("--rev", default=None, help="revision to build (default: currently installed ARTIQ revision)")
    act_build.add_argument("--log", action="store_true", help="Display the build log")
    act_build.add_argument("directory", help="output directory")
    act_build.add_argument("variant", nargs="?", default=None, help="variant to build (can be omitted if user is authorised to build only one)")
    act_passwd = action.add_parser("passwd", help="change password")
    act_get_variants = action.add_parser("get_variants", help="get available variants and expiry dates")
    args = parser.parse_args()

    cert = args.cert
    if cert is None:
        cert = get_artiq_cert()
    if cert is None:
        print("SSL certificate not found in ARTIQ. Specify manually using --cert.")
        sys.exit(1)

    if args.action == "passwd":
        password = getpass("Current password: ")
    else:
        password = getpass()

    client = Client(args.server, args.port, cert)
    try:
        if not client.login(args.username, password):
            print("Login failed")
            sys.exit(1)
        print("Logged in successfully.")
        if args.action == "passwd":
            print("Password must made of alphanumeric characters (a-z, A-Z, 0-9) and be at least 8 characters long.")
            password = getpass("New password: ")
            password_confirm = getpass("New password (again): ")
            while password != password_confirm:
                print("Passwords do not match")
                password = getpass("New password: ")
                password_confirm = getpass("New password (again): ")
            if not client.passwd(password):
                print("Failed to change password")
                sys.exit(1)
        elif args.action == "build":
            try:
                os.mkdir(args.directory)
            except FileExistsError:
                try:
                    if any(os.scandir(args.directory)):
                        print("Output directory already exists and is not empty. Please remove it and try again.")
                        sys.exit(1)
                except NotADirectoryError:
                    print("A file with the same name as the output directory already exists. Please remove it and try again.")
                    sys.exit(1)
            rev = args.rev
            if rev is None:
                rev = get_artiq_rev()
            if rev is None:
                print("Unable to determine currently installed ARTIQ revision. Specify manually using --rev.")
                sys.exit(1)
            result, contents = client.build(rev, args.variant, args.log)
            if result != "OK":
                if result == "UNAUTHORIZED":
                    print("You are not authorized to build this variant. Your firmware subscription may have expired. Contact helpdesk\x40m-labs.hk.")
                elif result == "TOOMANY":
                    print("Too many builds in a queue. Please wait for others to finish.")
                else:
                    print("Build failed: {}".format(result))
                sys.exit(1)
            zip_unarchive(contents, args.directory)
        elif args.action == "get_variants":
            data = client.get_variants()
            table = PrettyTable()
            table.field_names = ["Variant", "Expiry date"]
            table.add_rows(data)        
            print(table)
        else:
            raise ValueError
    finally:
        client.close()


if __name__ == "__main__":
    main()
