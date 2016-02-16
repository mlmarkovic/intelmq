#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function
import os
import sys
import json
import time
import shlex
import inspect
import psutil
import signal
import traceback
import argparse
from intelmq.lib.pipeline import PipelineFactory
from intelmq import DEFAULTS_CONF_FILE
from intelmq import PIPELINE_CONF_FILE
from intelmq import RUNTIME_CONF_FILE
from intelmq import STARTUP_CONF_FILE
from intelmq import SYSTEM_CONF_FILE
from intelmq.lib import utils


class Parameters(object):
    pass

PIDDIR = "/opt/intelmq/var/run/"
PIDFILE = "/opt/intelmq/var/run/{}.pid"

STATUSES = {
    'starting': 0,
    'running': 1,
    'stopping': 2,
    'stopped': 3,
}

MESSAGES = {
    'starting': 'Starting {}...',
    'running': '{} is running.',
    'stopped': '{} is stopped.',
    'stopping': 'Stopping {}...',
}

ERROR_MESSAGES = {
    'starting': '{} failed to START.',
    'running': '{} is still running.',
    'stopped': '{} was NOT RUNNING.',
    'stopping': '{} failed to STOP.',
    'noid': 'No or unconfigured ID was given, use --id',
    'notfound': '{} not found.'
}

LOG_LEVEL = {
    'DEBUG': 0,
    'INFO': 1,
    'ERROR': 2,
    'CRITICAL': 3,
}

RETURN_TYPES = ['text', 'json']
RETURN_TYPE = None


def log_list_queues(queues):
    if RETURN_TYPE == 'text':
        for queue, counter in sorted(queues.items()):
            logger.info("{} - {}".format(queue, counter))


def log_bot_error(status, *args):
    if RETURN_TYPE == 'text':
        logger.error(ERROR_MESSAGES[status].format(*args))


def log_bot_message(status, *args):
    if RETURN_TYPE == 'text':
        logger.info(MESSAGES[status].format(*args))


def log_botnet_error(status):
    if RETURN_TYPE == 'text':
        logger.error(ERROR_MESSAGES[status].format('Botnet'))


def log_botnet_message(status):
    if RETURN_TYPE == 'text':
        logger.info(MESSAGES[status].format('Botnet'))


def log_log_messages(messages):
    if RETURN_TYPE == 'text':
        for message in messages:
            print(' - '.join([message['date'], message['bot_id'],
                              message['log_level'], message['message']]))
            try:
                print(message['extended_message'])
            except KeyError:
                pass


def write_pidfile(bot_id, pid):
    filename = PIDFILE.format(bot_id)
    with open(filename, 'w') as fp:
        fp.write(str(pid))


def remove_pidfile(bot_id):
    filename = PIDFILE.format(bot_id)
    os.remove(filename)


def read_pidfile(bot_id):
    filename = PIDFILE.format(bot_id)
    if check_pidfile(bot_id):
        with open(filename, 'r') as fp:
            pid = fp.read()
        return pid.strip()
    return None


def check_pidfile(bot_id):
    filename = PIDFILE.format(bot_id)
    if os.path.isfile(filename):
        try:
            with open(filename, 'r') as fp:
                pid = fp.read()
            return int(pid.strip())
        except ValueError:
            return None
    return None


def start_process(bot_id, cmd):
    with open('/dev/null', 'w') as devnull:
        args = shlex.split(cmd)
        p = psutil.Popen(args, stdout=devnull, stderr=devnull)
        return p.pid


def stop_process(pid):
    p = psutil.Process(int(pid))
    p.send_signal(signal.SIGINT)


def status_process(pid):
    try:
        psutil.Process(int(pid))
        return True
    except psutil.NoSuchProcess:
        return False


class IntelMQContoller():

    def __init__(self):
        global RETURN_TYPE
        global logger
        logger = utils.log('intelmqctl', log_level='DEBUG')
        self.logger = logger

        APPNAME = "intelmqctl"
        VERSION = "0.0.0"
        DESCRIPTION = """
        description: intelmqctl is the tool to control intelmq system.

        Outputs are logged to /opt/intelmq/var/log/intelmqctl"""
        USAGE = '''
        intelmqctl --bot [start|stop|restart|status] --id=cymru-expert
        intelmqctl --botnet [start|stop|restart|status]
        intelmqctl --list [bots|queues]'''

        parser = argparse.ArgumentParser(
            prog=APPNAME,
            usage=USAGE,
            epilog=DESCRIPTION
        )

        group = parser.add_mutually_exclusive_group()
        group_list = group.add_mutually_exclusive_group()

        parser.add_argument('-v', '--version',
                            action='version', version=VERSION)
        parser.add_argument('--id', '-i',
                            dest='bot_id', default=None, help='bot ID')
        parser.add_argument('--type', '-t', choices=RETURN_TYPES,
                            default=RETURN_TYPES[0],
                            help='choose if it should return regular text or '
                                 'other forms of output')

        group_list.add_argument('--log', '-l',
                                metavar='[log-level]:[number-of-lines]',
                                default=None,
                                help='''Reads the last lines from bot log, or
                                from system log if no bot ID was given.
                                Log level should be one of DEBUG, INFO, ERROR
                                or CRTICAL. Default is INFO.
                                Number of lines defaults to 10, -1 gives all.

                                Reading from system log is not implemented yet.
                                ''')
        group_list.add_argument('--bot', '-b',
                                choices=['start', 'stop', 'restart', 'status'],
                                metavar='[start|stop|restart|status]',
                                default=None)
        group_list.add_argument('--botnet', '-n',
                                choices=['start', 'stop', 'restart', 'status'],
                                metavar='[start|stop|restart|status]',
                                default=None)
        group_list.add_argument('--list', '-s',
                                choices=['bots', 'queues'],
                                metavar='[bots|queues]',
                                default=None)
        group_list.add_argument('--clear', '-c', metavar='queue', default=None,
                                help='''Clears the given queue in broker''')

        self.args = parser.parse_args()

        if len(sys.argv) == 1:
            parser.print_help()

        RETURN_TYPE = self.args.type

        with open(STARTUP_CONF_FILE, 'r') as fp:
            self.startup = json.load(fp)

        with open(SYSTEM_CONF_FILE, 'r') as fp:
            self.system = json.load(fp)

        if not os.path.exists(PIDDIR):
            os.makedirs(PIDDIR)

        # stolen functions from the bot file
        # this will not work with various instances of REDIS
        self.parameters = Parameters()
        self.load_defaults_configuration()
        self.load_system_configuration()
        self.pipepline_configuration = utils.load_configuration(
            PIPELINE_CONF_FILE)
        self.runtime_configuration = utils.load_configuration(
            RUNTIME_CONF_FILE)
        self.startup_configuration = utils.load_configuration(
            STARTUP_CONF_FILE)

    def load_system_configuration(self):
        config = utils.load_configuration(SYSTEM_CONF_FILE)
        for option, value in config.items():
            setattr(self.parameters, option, value)

    def load_defaults_configuration(self):
        # Load defaults configuration section
        config = utils.load_configuration(DEFAULTS_CONF_FILE)
        for option, value in config.items():
            setattr(self.parameters, option, value)

    def auto_method_call(self, method):
        inspect_members = inspect.getmembers(self)
        for name, func in inspect_members:
            if name.startswith(method):
                return func

    def run(self):
        results = None
        if self.args.bot:
            method_name = "bot_" + self.args.bot
            call_method = self.auto_method_call(method_name)
            results = call_method(self.args.bot_id)

        elif self.args.botnet:
            method_name = "botnet_" + self.args.botnet
            call_method = self.auto_method_call(method_name)
            results = call_method()

        elif self.args.list:
            method_name = "list_" + self.args.list
            call_method = self.auto_method_call(method_name)
            results = call_method()

        elif self.args.log:
            results = self.read_log(self.args.log, self.args.bot_id)

        elif self.args.clear:
            results = self.clear_queue(self.args.clear,)

        if self.args.type == 'json':
            print(json.dumps(results))

    def bot_start(self, bot_id):
        if bot_id is None:
            log_bot_error('noid')
            return 'error'
        pid = read_pidfile(bot_id)
        if pid:
            if status_process(pid):
                log_bot_message('running', bot_id)
                return 'running'
            else:
                remove_pidfile(bot_id)
        log_bot_message('starting', bot_id)
        try:
            self.__bot_start(bot_id, self.startup[bot_id]['module'])
        except KeyError:
            log_bot_error('notfound', bot_id)
            return 'error'
        time.sleep(0.25)
        return self.bot_status(bot_id)

    def __bot_start(self, bot_id, module):
        """
        Start a bot by calling it as module.

        The python version/path can be specified by the INTELMQ_PYTHON
        environment variable. By default it's the default python binary.
        """
        cmd = "{} -m {} {}".format(os.getenv('INTELMQ_PYTHON', 'python'),
                                   module, bot_id)
        pid = start_process(bot_id, cmd)
        write_pidfile(bot_id, pid)

    def bot_stop(self, bot_id):
        pid = read_pidfile(bot_id)
        if not pid:
            log_bot_error('stopped', bot_id)
            return 'stopped'
        if not status_process(pid):
            remove_pidfile(bot_id)
            log_bot_error('stopped', bot_id)
            return 'stopped'
        log_bot_message('stopping', bot_id)
        self.__bot_stop(bot_id, pid)
        time.sleep(0.25)
        if status_process(pid):
            log_bot_error('running', bot_id)
            return 'running'
        log_bot_message('stopped', bot_id)
        return 'stopped'

    def __bot_stop(self, bot_id, pid):
        stop_process(pid)
        remove_pidfile(bot_id)

    def bot_restart(self, bot_id):
        status_stop = self.bot_stop(bot_id)
        status_start = self.bot_start(bot_id)
        return (status_stop, status_start)

    def bot_status(self, bot_id):
        pid = read_pidfile(bot_id)
        if pid and status_process(pid):
            log_bot_message('running', bot_id)
            return 'running'
        log_bot_message('stopped', bot_id)
        return 'stopped'

    def botnet_start(self):
        botnet_status = {}
        log_botnet_message('starting')
        for bot_id in sorted(self.startup.keys()):
            botnet_status[bot_id] = self.bot_start(bot_id)
        log_botnet_message('running')
        return botnet_status

    def botnet_stop(self):
        botnet_status = {}
        log_botnet_message('stopping')
        for bot_id in sorted(self.startup.keys()):
            botnet_status[bot_id] = self.bot_stop(bot_id)
        log_botnet_message('stopped')
        return botnet_status

    def botnet_restart(self):
        botnet_status = {}
        log_botnet_message('stopping')
        for bot_id in sorted(self.startup.keys()):
            botnet_status[bot_id] = tuple(self.bot_stop(bot_id))
        time.sleep(3)
        log_botnet_message('stopped')
        log_botnet_message('starting')
        for bot_id in sorted(self.startup.keys()):
            botnet_status[bot_id] += tuple(self.bot_start(bot_id))
        log_botnet_message('running')
        return botnet_status

    def botnet_status(self):
        botnet_status = {}
        for bot_id in sorted(self.startup.keys()):
            botnet_status[bot_id] = self.bot_status(bot_id)
        return botnet_status

    def list_bots(self):
        print("List of Bots:\n-------------")
        for bot_id in sorted(self.startup.keys()):
            print("\nBot ID: {}\nDescription: {}"
                  "".format(bot_id, self.startup[bot_id]['description']))
        return [{'id': bot_id,
                 'description': self.startup[bot_id]['description']}
                for bot_id in sorted(self.startup.keys())]

    def list_queues(self):
        source_queues = set()
        destination_queues = set()

        for key, value in self.pipepline_configuration.items():
            if 'source-queue' in value:
                source_queues.add(value['source-queue'])
            if 'destination-queues' in value:
                destination_queues.update(value['destination-queues'])

        pipeline = PipelineFactory.create(self.parameters)
        pipeline.set_queues(source_queues, "source")
        pipeline.connect()

        queues = source_queues.union(destination_queues)
        counters = pipeline.count_queued_messages(queues)
        log_list_queues(counters)

        return_dict = dict()
        for bot_id, info in self.pipepline_configuration.items():
            return_dict[bot_id] = dict()

            if 'source-queue' in info:
                return_dict[bot_id]['source_queue'] = (
                    info['source-queue'], counters[info['source-queue']])

            if 'destination-queues' in info:
                return_dict[bot_id]['destination_queues'] = list()
                for dest_queue in info['destination-queues']:
                    return_dict[bot_id]['destination_queues'].append(
                        (dest_queue, counters[dest_queue]))

        return return_dict

    def clear_queue(self, queue):
        """
        Clears an exiting queue.

        First checks if the queue does exist in the pipeline configuration.
        """
        logger.info("Clearing queue {}".format(queue))
        source_queues = set()
        destination_queues = set()
        for key, value in self.pipepline_configuration.items():
            if 'source-queue' in value:
                source_queues.add(value['source-queue'])
            if 'destination-queues' in value:
                destination_queues.update(value['destination-queues'])

        pipeline = PipelineFactory.create(self.parameters)
        pipeline.set_queues(source_queues, "source")
        pipeline.connect()

        queues = source_queues.union(destination_queues)
        if queue not in queues:
            logger.error("Queue {} does not exist!".format(queue))
            return 'not-found'

        try:
            pipeline.clear_queue(queue)
            logger.info("Successfully cleared queue {}".format(queue))
            return 'success'
        except Exception:
            logger.error("Error while clearing queue {}:\n{}"
                         "".format(queue, traceback.format_exc()))
            return 'error'

    def read_log(self, log_level, bot_id):
        # TODO: Parse number of lines
        split_log_level = log_level.split(':')

        if len(split_log_level) != 2:
            logger.error("Invalid parameter for log, defaulting to 'INFO:10'")
            number_of_lines = 10
            log_level = LOG_LEVEL['INFO']
        else:
            try:
                number_of_lines = int(split_log_level[1])
            except ValueError:
                number_of_lines = 10
            if not len(split_log_level[0]):
                log_level = LOG_LEVEL['INFO']
            else:
                try:
                    log_level = LOG_LEVEL[split_log_level[0].upper()]
                except KeyError:
                    logger.error("Invalid log_level. Must be one of {}"
                                 "".format(', '.join(LOG_LEVEL.keys())))
                    return[]

        if bot_id is None:
            return self.read_system_log(log_level, number_of_lines)
        else:
            return self.read_bot_log(bot_id, log_level, number_of_lines)

    def read_system_log(self, log_level, number_of_lines):
        logger.error("Reading from system log is not implemented yet")

    def read_bot_log(self, bot_id, log_level, number_of_lines):
        bot_log_path = os.path.join(self.system['logging_path'],
                                    bot_id + '.log')
        if not os.path.isfile(bot_log_path):
            logger.error("Log path not found: {}".format(bot_log_path))
            return []

        messages = list()

        message_overflow = ''
        message_count = 0

        for line in utils.reverse_readline(bot_log_path):
            log_message = utils.parse_logline(line)

            if type(log_message) is not dict:
                message_overflow = '\n'.join([line, message_overflow])
                continue
            if LOG_LEVEL[log_message['log_level']] < log_level:
                continue

            if message_overflow:
                log_message['extended_message'] = message_overflow
                message_overflow = ''

            message_count += 1
            messages.append(log_message)

            if message_count >= number_of_lines and number_of_lines != -1:
                break

        log_log_messages(messages[::-1])
        return messages[::-1]


def main():
    x = IntelMQContoller()
    x.run()

if __name__ == "__main__":
    main()
