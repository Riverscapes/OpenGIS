from __future__ import annotations
import math
import csv
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from time import sleep
import psutil
import matplotlib.pyplot as plt
from rscommons import Logger

# https://medium.com/survata-engineering-blog/monitoring-memory-usage-of-a-running-python-program-49f027e3d1ba


class ProcStats():
    headers = ['datetime', 'cpu_percent', 'resident_memory', 'virtual_memory', 'children', 'children_resident', 'children_virtual']

    def __init__(self, cpu_percent, rss_raw: int, vms_raw: int, children: int, children_rss_raw: int, children_vms_raw: int):
        self.datetime = datetime.now()
        self.cpu_percent = cpu_percent
        self.children = children
        self.rss = rss_raw / float(2 ** 20)
        self.vms = vms_raw / float(2 ** 20)
        self.children_rss = children_rss_raw / float(2 ** 20)
        self.children_vms = children_vms_raw / float(2 ** 20)

    def row(self):
        return [
            self.datetime.strftime('%Y-%m-%d %H:%M:%S'),
            math.ceil(self.cpu_percent),
            "{:.2f}".format(self.rss),
            "{:.2f}".format(self.vms),
            "{}".format(self.children),
            "{:.2f}".format(self.rss),
            "{:.2f}".format(self.vms)
        ]

    def toString(self):
        return "datetime: {}, cpu_percent: {}, mem_resident: {}Mb, mem_virtual: {}Mb, num_children: {}, mem_children_resident: {}Mb, mem_children_virtual: {}Mb".format(
            self.datetime.strftime('%Y-%m-%d %H:%M:%S'),
            math.ceil(self.cpu_percent),
            "{:.2f}".format(self.rss),
            "{:.2f}".format(self.vms),
            "{}".format(self.children),
            "{:.2f}".format(self.rss),
            "{:.2f}".format(self.vms)
        )

    def max(self, tick: ProcStats):
        self.cpu_percent = max(self.cpu_percent, tick.cpu_percent)
        self.children = max(self.children, tick.children)
        self.rss = max(self.rss, tick.rss)
        self.vms = max(self.vms, tick.vms)
        self.children_rss = max(self.children_rss, tick.children_rss)
        self.children_vms = max(self.children_vms, tick.children_vms)


class MemoryMonitor:
    def __init__(self, logfile: str, loop_delay=1):
        self.keep_measuring = True
        self.filepath = logfile
        self.loop_delay = loop_delay
        self.process = psutil.Process(os.getpid())
        self.headers_written = False
        self.max_stats = ProcStats(0, 0, 0, 0, 0, 0)

    def write_line(self, arr, mode='a'):
        with open(self.filepath, mode, newline='', encoding='utf-8') as csvfile:
            csvwriter = csv.writer(csvfile)
            csvwriter.writerow(arr)

    def getstats(self) -> ProcStats:
        cpu_percent = self.process.cpu_percent()
        mem_info = self.process.memory_info()
        children = 0
        children_rss = 0
        children_vms = 0

        for child in self.process.children():
            child_mem = child.memory_info()
            children_rss += child_mem.rss
            children_vms += child_mem.vms
            children += 1

        stats = ProcStats(cpu_percent, mem_info.rss, mem_info.vms, children, children_rss, children_vms)
        self.max_stats.max(stats)
        return stats

    def measure_usage(self):
        self.write_line(ProcStats.headers, 'w')
        while self.keep_measuring:
            self.write_line(self.getstats().row())
            sleep(self.loop_delay)
        return self.max_stats

    def write_plot(self, imgpath: str):
        x = []
        data = {}
        with open(self.filepath) as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                for key in row.keys():
                    if key == 'datetime':
                        x.append(row[key])
                    else:
                        if key in ['children', 'cpu_percent']:
                            val = int(row[key])
                        else:
                            val = float(row[key])
                        if key not in data:
                            data[key] = [val]
                        else:
                            data[key].append(val)

        chart_title = 'Process stats'
        xlabel = 'time'
        ylabel = 'Mb'

        plt.clf()

        fig, ax = plt.subplots()
        ax.title.set_text(chart_title)
        ax2 = ax.twinx()
        #  ['datetime', 'cpu_percent', 'resident_memory', 'virtual_memory', 'children', 'children_resident', 'children_virtual']
        for key in ['children_resident', 'children_virtual', 'resident_memory', 'virtual_memory']:
            if key in data:
                ax.plot(x, data[key], label=key)
            ax2._get_lines.get_next_color()

        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)

        for key in ['cpu_percent', 'children']:
            if key in data:
                ax2.plot(x, data[key], label=key)

        ax.legend(loc='lower left')
        ax2.legend(loc='lower right')

        freq = math.floor(len(x) / 10)
        if freq == 0:
            freq = 1
        ax.set_xticks(x[::freq])
        ax.set_xticklabels(x[::freq], rotation=45)
        ax.grid(True)

        # plt.tight_layout()
        fig.set_size_inches(8, 6)
        fig.savefig(imgpath, format='png', dpi=300)


def ThreadRun(callback, memlogfile: str, *args, **kwargs):
    log = Logger('Debug')
    memmon = MemoryMonitor(memlogfile, 1)
    result = None
    max_obj = None
    with ThreadPoolExecutor() as executor:
        mem_thread = executor.submit(memmon.measure_usage)
        try:
            fn_thread = executor.submit(callback, *args, **kwargs)
            result = fn_thread.result()
        except Exception as e:
            log.error("Error executing code: {}".format(e))
        finally:
            memmon.keep_measuring = False
            max_obj = mem_thread.result()
            log.debug('MaxStats: {}'.format(max_obj))
    try:
        memmon.write_plot(os.path.splitext(memlogfile)[0] + '.png')
    except Exception as e:
        log.error('Error Writing memory plot: {}'.format(e))

    return result, max_obj.toString()
