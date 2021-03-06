#!/usr/bin/env python
# -*- coding: utf-8 -*-

import multiprocessing
import time

from alex.components.nlg.common import nlg_factory, get_nlg_type

from alex.components.hub.messages import Command, DMDA, TTSText
from alex.components.dm.exceptions import DMException

from alex.utils.procname import set_proc_name


class NLG(multiprocessing.Process):
    """The NLG component receives a dialogue act generated by the dialogue manager and then it
    converts the act into the text.

    This component is a wrapper around multiple NLG components which handles multiprocessing
    communication.
    """

    def __init__(self, cfg, commands, dialogue_act_in, text_out, close_event):
        multiprocessing.Process.__init__(self)

        self.cfg = cfg
        self.commands = commands
        self.dialogue_act_in = dialogue_act_in
        self.text_out = text_out
        self.close_event = close_event

        nlg_type = get_nlg_type(cfg)
        self.nlg = nlg_factory(nlg_type, cfg)

    def process_da(self, da):
        if da != "silence()":
            text = self.nlg.generate(da)

            if self.cfg['NLG']['debug']:
                s = []
                s.append("NLG Output")
                s.append("-"*60)
                s.append(text)
                s.append("")
                s = '\n'.join(s)
                self.cfg['Logging']['system_logger'].debug(s)

            self.cfg['Logging']['session_logger'].text("system", text)

            self.commands.send(Command('nlg_text_generated()', 'NLG', 'HUB'))
            self.commands.send(TTSText(text))
            self.text_out.send(TTSText(text))
        else:
            # the input dialogue is silence. Therefore, do not generate eny output.
            if self.cfg['NLG']['debug']:
                s = []
                s.append("NLG Output")
                s.append("-"*60)
                s.append("DM sent 'silence()' therefore generating nothing")
                s.append("")
                s = '\n'.join(s)
                self.cfg['Logging']['system_logger'].debug(s)

            self.cfg['Logging']['session_logger'].text("system", "_silence_")

            self.commands.send(Command('nlg_text_generated()', 'NLG', 'HUB'))

    def process_pending_commands(self):
        """Process all pending commands.

        Available commands:
          stop() - stop processing and exit the process
          flush() - flush input buffers.
            Now it only flushes the input connection.

        Return True if the process should terminate.
        """

        while self.commands.poll():
            command = self.commands.recv()
            if self.cfg['NLG']['debug']:
                self.cfg['Logging']['system_logger'].debug(command)

            if isinstance(command, Command):
                if command.parsed['__name__'] == 'stop':
                    return True

                if command.parsed['__name__'] == 'flush':
                    # discard all data in in input buffers
                    while self.dialogue_act_in.poll():
                        data_in = self.dialogue_act_in.recv()

                    # the NLG component does not have to be flushed
                    #self.nlg.flush()

                    self.commands.send(Command("flushed()", 'NLG', 'HUB'))

                    return False
            elif isinstance(command, DMDA):
                self.process_da(command.da)

        return False

    def read_dialogue_act_write_text(self):
        if self.dialogue_act_in.poll():
            data_da = self.dialogue_act_in.recv()

            if isinstance(data_da, DMDA):
                self.process_da(data_da.da)
            elif isinstance(data_da, Command):
                self.cfg['Logging']['system_logger'].info(data_da)
            else:
                raise DMException('Unsupported input.')

    def run(self):
        try:
            set_proc_name("Alex_NLG")
            self.cfg['Logging']['session_logger'].cancel_join_thread()

            while 1:
                # Check the close event.
                if self.close_event.is_set():
                    print 'Received close event in: %s' % multiprocessing.current_process().name
                    return

                time.sleep(self.cfg['Hub']['main_loop_sleep_time'])

                s = (time.time(), time.clock())

                # process all pending commands
                if self.process_pending_commands():
                    return

                # process the incoming DM dialogue acts
                self.read_dialogue_act_write_text()

                d = (time.time() - s[0], time.clock() - s[1])
                if d[0] > 0.200:
                    print "EXEC Time inner loop: NLG t = {t:0.4f} c = {c:0.4f}\n".format(t=d[0], c=d[1])

        except KeyboardInterrupt:
            print 'KeyboardInterrupt exception in: %s' % multiprocessing.current_process().name
            self.close_event.set()
            return
        except:
            self.cfg['Logging']['system_logger'].exception('Uncaught exception in NLG process.')
            self.close_event.set()
            raise

        print 'Exiting: %s. Setting close event' % multiprocessing.current_process().name
        self.close_event.set()
