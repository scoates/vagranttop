#!/usr/bin/env python
# coding=utf-8

# Copyright (c) 2009, Giampaolo Rodola'. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# original Author: Giampaolo Rodola' <g.rodola@gmail.com>

# from: https://github.com/giampaolo/psutil/blob/master/examples/top.py

# refactor and vagrant-specific parts by Sean Coates <sean@seancoates.com>

from datetime import datetime, timedelta
import os
import time
import sys
try:
    import curses
except ImportError:
    sys.exit('platform not supported')
import subprocess

import psutil


class Top:

    def __init__(self, win, ssh=False):
        self.win = win
        self.lineno = 0
        self.sort_col = 'cpu_percent'
        self.sort_reverse = True
        self.graceful_exit = False
        self._get_vagrant_machines()
        self._get_vbox_running_vms()
        self.ssh = ssh


    def print_line(self, line, highlight=False):
        """A thin wrapper around curses's addstr()."""
        try:
            if highlight:
                line += " " * (self.win.getmaxyx()[1] - len(line))
                self.win.addstr(self.lineno, 0, line, curses.A_REVERSE)
            else:
                self.win.addstr(self.lineno, 0, line, 0)
        except curses.error:
            self.lineno = 0
            self.win.refresh()
            raise
        else:
            self.lineno += 1
    # --- /curses stuff


    def bytes2human(self, n):
        """
        >>> bytes2human(10000)
        '9K'
        >>> bytes2human(100001221)
        '95M'
        """
        symbols = ('K', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y')
        prefix = {}
        for i, s in enumerate(symbols):
            prefix[s] = 1 << (i + 1) * 10
        for s in reversed(symbols):
            if n >= prefix[s]:
                value = int(float(n) / prefix[s])
                return '%s%s' % (value, s)
        return "%sB" % n


    def poll(self, interval):
        # sleep some time; sleep in small chunks so we can poll the input
        for i in range(0, 99):
            if self.check_input():
                break
            time.sleep(interval/100.0)
        procs = []
        procs_status = {}
        for p in psutil.process_iter():
            try:
                p.dict = p.as_dict([
                    'username', 'memory_info', 'memory_percent',
                    'cpu_percent', 'cpu_times', 'name', 'status',
                    'cwd', 'cmdline'
                ])
                try:
                    procs_status[p.dict['status']] += 1
                except KeyError:
                    procs_status[p.dict['status']] = 1
            except psutil.NoSuchProcess:
                pass
            else:
                if 'VBoxHeadless' == p.dict['name']:
                    p.dict['vm'] = None
                    vagrant_comment = self.get_vagrant_comment(p.dict['cmdline'])

                    if vagrant_comment not in self.running_vms:
                        # attempt to refesh
                        self._get_vagrant_machines()
                        self._get_vbox_running_vms()

                    if vagrant_comment in self.running_vms:
                        p.dict['vm'] = self.vagrant_machines[self.running_vms[vagrant_comment]]
                        p.dict['vm_dir'] = p.dict['vm']['dir_name']
                        p.dict['vm_name'] = p.dict['vm']['name']
                        p.dict['vm_id'] = p.dict['vm']['id']
                        p.dict['vm_load'] = self._get_vagrant_load(p.dict['vm']['id'])
                    else:
                        p.dict['vm_id'] = ""
                        p.dict['vm_dir'] = ""
                        p.dict['vm_name'] = vagrant_comment
                        p.dict['vm_load'] = ""

                    #p.dict['vm_name'] = self.get_vagrant_comment(p.dict['cmdline'])
                    # TIME+ column shows process CPU cumulative time and it
                    # is expressed as: "mm:ss.ms"
                    if p.dict['cpu_times'] is not None:
                        p.dict['time_sum'] = sum(p.dict['cpu_times'])
                    else:
                        p.dict['time_sum'] = ''
                    p.dict['pid'] = p.pid
                    procs.append(p)

        # return processes sorted by CPU percent usage
        processes = sorted(procs, key=lambda p: p.dict[self.sort_col],
                           reverse=self.sort_reverse)
        return (processes, procs_status)


    def print_header(self, procs_status, num_procs):
        """Print system-related info, above the process list."""

        def get_dashes(perc):
            dashes = "|" * int((float(perc) / 10 * 4))
            empty_dashes = " " * (40 - len(dashes))
            return dashes, empty_dashes

        # cpu usage
        percs = psutil.cpu_percent(interval=0, percpu=True)
        for cpu_num, perc in enumerate(percs):
            dashes, empty_dashes = get_dashes(perc)
            self.print_line(" CPU%-2s [%s%s] %5s%%" % (cpu_num, dashes, empty_dashes,
                                                  perc))
        mem = psutil.virtual_memory()
        dashes, empty_dashes = get_dashes(mem.percent)
        used = mem.total - mem.available
        line = " Mem   [%s%s] %5s%% %6s/%s" % (
            dashes, empty_dashes,
            mem.percent,
            str(int(used / 1024 / 1024)) + "M",
            str(int(mem.total / 1024 / 1024)) + "M"
        )
        self.print_line(line)

        # swap usage
        swap = psutil.swap_memory()
        dashes, empty_dashes = get_dashes(swap.percent)
        line = " Swap  [%s%s] %5s%% %6s/%s" % (
            dashes, empty_dashes,
            swap.percent,
            str(int(swap.used / 1024 / 1024)) + "M",
            str(int(swap.total / 1024 / 1024)) + "M"
        )
        self.print_line(line)

        # processes number and status
        st = []
        for x, y in procs_status.items():
            if y:
                st.append("%s=%s" % (x, y))
        st.sort(key=lambda x: x[:3] in ('run', 'sle'), reverse=1)
        self.print_line(" Processes: %s (%s)" % (num_procs, ' '.join(st)))
        # load average, uptime
        uptime = datetime.now() - datetime.fromtimestamp(psutil.boot_time())
        av1, av2, av3 = os.getloadavg()
        line = " Load average: %.2f %.2f %.2f  Uptime: %s" \
            % (av1, av2, av3, str(uptime).split('.')[0])
        self.print_line(line)


    def refresh_window(self, procs, procs_status):
        """Print results on screen by using curses."""
        curses.endwin()

        h, w = self.win.getmaxyx()
        # pad with way too many spaces to be sure we fill
        templ = "{pid: >7} {cpu_percent: >5} {memory_percent: >5} {time_sum: >9} {vm_id: <8} {vm_load: >7} {vm_dir: <10} {vm_name: <" + str(w) + "}"
        self.win.erase()
        header_dict = {
            'pid': "PID",
            'cpu_percent': "CPU%",
            'memory_percent': "MEM%",
            'time_sum': " TIME+   ",
            'vm_dir': ' VM DIR',
            'vm_name': ' VM NAME',
            'vm_id': ' VM ID',
            'vm_load': 'VM LOAD'
        }
        if self.sort_reverse:
            sort_char = u">"
        else:
            sort_char = u"<"
        if self.sort_col in header_dict:
            header_dict[self.sort_col] = sort_char + header_dict[self.sort_col].lstrip()
        header = templ.format(**header_dict)
        self.print_header(procs_status, len(procs))
        self.print_line("")
        self.print_line(header, highlight=True)

        full = False
        for p in procs:
            if p.dict['memory_percent'] is not None:
                p.dict['memory_percent'] = round(p.dict['memory_percent'], 1)
            else:
                p.dict['memory_percent'] = ''
            if p.dict['cpu_percent'] is None:
                p.dict['cpu_percent'] = ''
            if p.dict['username']:
                username = p.dict['username'][:8]
            else:
                username = ""

            try:
                time_sum = int(p.dict['time_sum'])
            except ValueError:
                time_sum = 0

            try:
                vm_id = " " + p.dict['vm_id']
            except TypeError:
                vm_id = " (none)"

            line = templ.format(
                pid=p.pid,
                cpu_percent=p.dict['cpu_percent'],
                memory_percent=p.dict['memory_percent'],
                time_sum="{0:0>8}".format(str(timedelta(seconds=time_sum))),
                vm_dir=" " + p.dict['vm_dir'],
                vm_name=" " + p.dict['vm_name'],
                vm_id=vm_id,
                vm_load=p.dict['vm_load']
            )
            try:
                self.print_line(line)
            except curses.error:
                full = True
                break

        if not full:
            # pad with blank lines
            while True:
                try:
                    self.print_line("")
                except curses.error:
                    break

        self.win.refresh()


    def check_input(self):
        try:
            k = self.win.getkey()
        except curses.error:
            return False

        if "q" == k:
            self.graceful_exit = True
            return True

        key_map = {
            "c": 'cpu_percent',
            "m": 'memory_percent',
            "t": 'time_sum',
            "p": 'pid',
            "d": "vm_dir",
            "n": 'vm_name',
            "i": 'vm_id',
        }
        if k in key_map:
            if self.sort_col == key_map[k]:
                # already selected, reverse direction
                self.sort_reverse = not self.sort_reverse
            else:
                self.sort_col = key_map[k]

        return True


    def loop(self):
        try:
            interval = 0
            while not self.graceful_exit:
                args = self.poll(interval)
                self.refresh_window(*args)
                interval = 1
        except (KeyboardInterrupt, SystemExit):
            pass


    def get_vagrant_comment(self, cmdline):
        found_comment = False
        vagrant_comment = None
        if cmdline:
            for arg in cmdline:
                print arg
                if found_comment:
                    return arg
                if '--comment' == arg:
                    found_comment = True


    def _get_vagrant_machines(self):
        self.vagrant_machines = get_vagrant_machines()


    def _get_vbox_running_vms(self):
        self.running_vms = get_vbox_running_vms()

    def _get_vagrant_load(self, vagrant_id):
        if self.ssh:
            return get_vagrant_load(vagrant_id)
        else:
            return "?"


def get_vbox_running_vms():
    cmd = subprocess.check_output(['VBoxManage', 'list', 'runningvms'])
    vms = {}

    for line in [l.strip() for l in cmd.split("\n") if l.strip()]:
        l = line.strip().split(' ')
        vms[l[0].strip('"')] = l[1].strip('}{')

    return vms


def get_vagrant_machines():
    cmd = subprocess.check_output(['vagrant', 'global-status'])
    machines = {}

    # capture first line as headers
    # skip first 2 lines; then capture until empty line
    # this is fragile, but `--machine-readable` returns empty on my vagrant
    header_line = cmd.split("\n")[0]
    header = [s.strip() for s in header_line.split(' ') if s.strip()]
    for line in [l.strip() for l in cmd.split("\n")][2:]:
        if line == "":
            break
        parts = [s.strip() for s in line.split(' ') if s.strip(' ')]
        line_dict = {}

        # capture header columns as names
        for col_num in range(0, len(header)):
            line_dict[header[col_num]] = parts[col_num]

        line_dict['dir_name'] = os.path.split(line_dict['directory'])[-1]

        vagrant_path = os.path.join(
            line_dict['directory'],
            '.vagrant/machines',
            line_dict['name'],
            line_dict['provider']
        )
        line_dict['index_uuid'] = open(os.path.join(vagrant_path, 'index_uuid')).read(1000)
        provider_id = open(os.path.join(vagrant_path, 'id')).read(1000)
        machines[provider_id] = line_dict
    return machines


def get_vagrant_load(vagrant_id, tmp_dir='/tmp/vagranttop'):
    # vagrant needs a Vagrantfile + environment for ssh to work, even if it's empty
    if not os.path.isdir(tmp_dir):
        os.mkdir(tmp_dir)
        open(os.path.join(tmp_dir, 'Vagrantfile'), 'a').close()

    cmd = subprocess.check_output(
        ['vagrant', 'ssh', vagrant_id, '-c', 'cat /proc/loadavg'],
        cwd=tmp_dir,
        stderr=open(os.devnull, 'wb')
    )
    return cmd.strip().split(' ')[0]


def main(win):
    ssh = ('DO_SSH' in os.environ and os.environ['DO_SSH'])
    win.nodelay(True)
    curses.curs_set(False)
    top = Top(win, ssh=ssh)
    top.loop()


if __name__ == '__main__':
    curses.wrapper(main)
