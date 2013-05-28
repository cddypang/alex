#!/usr/bin/env python
# -*- coding: utf-8 -*-
# This code is PEP8-compliant. See http://www.python.org/dev/peps/pep-0008.

import numpy as np

import codecs
from collections import defaultdict, namedtuple
import copy
from itertools import izip, islice, product
from operator import itemgetter, mul

from alex import utils
from alex.utils.various import flatten
from alex.components.slu.exception import SLUException
from alex.ml.hypothesis import Hypothesis, NBList, NBListException
# TODO: The following import is a temporary workaround for moving classes
# originally defined here to that module.  Instead, refer to the new module's
# definitions everywhere where this module would have been used.
from alex.ml.features import *


SENTENCE_START = u'<s>'
SENTENCE_END = u'</s>'


def load_utterances(utt_fname, limit=None, encoding='UTF-8'):
    """Loads a dictionary of utterances from a given file. The file is assumed
    to contain lines of the following form:

    [whitespace..]<key>[whitespace..]=>[whitespace..]<utterance>[whitespace..]

    Arguments:
        utt_fname -- path towards the file to read the utterances from
        limit -- limit on the number of utterances to read
        encoding -- the file encoding

    Returns a dictionary with utterances (instances of Utterance) as values.

    """
    with codecs.open(utt_fname, encoding=encoding) as utt_file:
        utterances = {}
        for line_id, line in enumerate(islice(utt_file, 0, limit)):
            line = line.strip()
            if not line:
                continue

            parts = line.split("=>")

            if len(parts) == 2:
                key = parts[0].strip()
                utt = parts[1].strip()
            else:
                key = "%d" % line_id
                utt = line

            utterances[key] = Utterance(utt)
    return utterances


class UtteranceException(SLUException):
    pass


class UtteranceNBListException(SLUException):
    pass


class UtteranceConfusionNetworkException(SLUException):
    pass


class ASRHypothesis(Hypothesis):
    """This is the base class for all forms of probabilistic ASR hypotheses
    representations."""
    pass


class Utterance(object):
    # TODO: Since Utterance basically represents a (is-a) list, it should
    # inherit from the builtin `list', I reckon. This might be a bit tricky,
    # though, because of the way built-in types are constructed.

    def __init__(self, surface):
        self._utterance = surface.split()
        self._wordset = set(self._utterance)

    def __str__(self):
        return ' '.join(word.encode('utf-8') for word in self._utterance)

    def __unicode__(self):
        return u' '.join(self._utterance)

    def __contains__(self, phrase):
        return self.find(phrase) != -1

    def __lt__(self, other):
        return self._utterance < other.utterance

    def __le__(self, other):
        return self._utterance <= other.utterance

    def __eq__(self, other):
        if isinstance(other, Utterance):
            return self._utterance == other.utterance
        elif isinstance(other, basestring):
            return self._utterance == other.split()
        return False

    def __ne__(self, other):
        return not self.__eq__(other.utterance)

    def __gt__(self, other):
        return self._utterance > other.utterance

    def __ge__(self, other):
        return self._utterance >= other.utterance

    def __len__(self):
        return len(self._utterance)

    def __getitem__(self, idx):
        return self._utterance[idx]

    def __iter__(self):
        for word in self._utterance:
            yield word

    @property
    def utterance(self):
        return self._utterance

    @utterance.setter
    def utterance(self, utt):
        self._utterance = utt
        self._wordset = set(self._utterance)

    def isempty(self):
        return len(self._utterance) == 0

    # TODO cache(1)
    def index(self, phrase):
        """Returns the word index of the start of first occurence of `phrase'
        within this utterance. If none is found, ValueError is raised.

        Arguments:
            phrase -- a list of words constituting the phrase sought

        """
        index = self.find(phrase)
        if index == -1:
            # No match found.
            raise ValueError('Missing "{phrase}" in "{utt}"'
                             .format(phrase=phrase, utt=self._utterance))
        return index

    def find(self, phrase):
        """Returns the word index of the start of first occurence of `phrase'
        within this utterance. If none is found, returns -1.

        Arguments:
            phrase -- a list of words constituting the phrase sought

        """
        assert len(phrase) > 0
        # All through this method, we assume a short length of `phrase', with
        # little or none repeated word tokens.

        # For phrases whose all words are not present anywhere, return quickly.
        if not all(word in self._wordset for word in phrase):
            # No match found.
            return -1

        # XXX Boyer-Moore could be a faster matching algorithm if the initial
        # overhead is not too big (which I am afraid it is).

        # Compute the maximal skip in case of incomplete match.
        initial = phrase[0]
        for word_count, word in enumerate(phrase[1:], start=1):
            if word == initial:
                max_skip = word_count
                break
        else:
            max_skip = len(phrase)

        # last index where the match can start
        last_idx = len(self._utterance) - len(phrase)
        # Iterate over the utterance.
        match_idx = 0
        while match_idx <= last_idx:
            # If the initial word matches,
            if self._utterance[match_idx] == initial:
                # Check the subsequent words too.
                for phrase_idx in xrange(1, len(phrase)):
                    if self._utterance[match_idx + phrase_idx] !=\
                            phrase[phrase_idx]:
                        break
                else:
                    # Match found.
                    return match_idx
                # If subsequent words do not match, skip them.
                match_idx += min(max_skip, phrase_idx)
            else:
                match_idx += 1
        # No match found.
        return -1

    def replace(self, orig, replacement, return_startidx=False):
        orig_pos = self.find(orig)
        if orig_pos == -1:
            # If `orig' does not occur in self, do nothing, return self.
            ret_utt = self
        else:
            # If `orig' does occur in self, construct a new utterance with `orig'
            # replaced by `replacement' and return that.
            ret_utt = Utterance('')
            if not isinstance(replacement, list):
                replacement = list(replacement)
            ret_utt.utterance = (self._utterance[:orig_pos] +
                                 replacement +
                                 self._utterance[orig_pos + len(orig):])
        return (ret_utt, orig_pos) if return_startidx else ret_utt

    def lower(self):
        """Lowercases words of this utterance.

        BEWARE, this method is destructive, it lowercases self.

        """
        for widx, word in enumerate(self._utterance):
            self._utterance[widx] = word.lower()
        self._wordset = set(self._utterance)
        return self

    def iter_with_boundaries(self):
        """Iterates the sequence [SENTENCE_START, word1, ..., wordlast,
        SENTENCE_END].

        """
        yield SENTENCE_START
        for word in self._utterance:
            yield word
        yield SENTENCE_END

    def iter_ngrams(self, n, with_boundaries=False):
        min_len = n - with_boundaries * 2
        # If the n-gram so-so fits into the utterance.
        if len(self._utterance) <= min_len:
            if len(self._utterance) == min_len:
                if with_boundaries:
                    yield [SENTENCE_START] + self._utterance + [SENTENCE_END]
                else:
                    yield self._utterance[:]
            return
        # Usual cases.
        if with_boundaries and len(self._utterance) > min_len:
            yield [SENTENCE_START] + self._utterance[:n - 1]
        for start_idx in xrange(len(self._utterance) - n + 1):
            yield self._utterance[start_idx:start_idx + n]
        if with_boundaries:
            yield self._utterance[-(n - 1):] + [SENTENCE_END]


# TODO Document.
# TODO Extend to AbstractedLattice.
# TODO Write tests.
class AbstractedUtterance(Utterance, Abstracted):
    other_val = ('[OTHER]', )

    def __init__(self, surface):
        self._abstr_idxs = list()  # sorted in an increasing order
        Utterance.__init__(self, surface)
        Abstracted.__init__(self)

    def __cmp__(self, other):
        if isinstance(other, AbstractedUtterance):
            my_key = (self._utterance, self._abstr_idxs)
            their_key = (other.utterance, other._abstr_idxs)
            return ((my_key >= their_key) - (their_key >= my_key))
        else:
            return 1

    def __hash__(self):
        return hash((tuple(self._utterance), tuple(self._abstr_idxs)))

    @classmethod
    def from_utterance(cls, utterance):
        """Constructs a new AbstractedUtterance from an existing Utterance."""
        abutt = AbstractedUtterance('')
        abutt.utterance = utterance.utterance
        return abutt

    @classmethod
    def make_other(cls, type_):
        return (u'{t}-OTHER'.format(t=type_[0]), )

    def join_typeval(self, type_, val):
        return (self.splitter.join((type_[0], ' '.join(val))), )

    def iter_typeval(self):
        for idx in self._abstr_idxs:
            yield (self._utterance[idx], )

    def iter_triples(self):
        for combined_el, in self.iter_typeval():
            split = combined_el.split(self.splitter, 2)
            try:
                type_, value = split
            except ValueError:
                value = ''
                type_ = split[0] if combined_el else ''
            # XXX Change the order of return values to combined_el, type_,
            # value.
            yield (combined_el, ), tuple(value.split(' ')), (type_, )

    def phrase2category_label(self, phrase, catlab):
        """Replaces the phrase given by `phrase' by a new phrase, given by
        `catlab'.  Assumes `catlab' is an abstraction for `phrase'.

        """
        combined_el = self.splitter.join((' '.join(catlab),
                                          ' '.join(phrase)))
        return self.replace(phrase, (combined_el, ))

    def replace(self, orig, replacement):
        """Replaces the phrase given by `orig' by a new phrase, given by
        `replacement'.

        """
        replaced, startidx = Utterance.replace(self, orig, replacement,
                                               return_startidx=True)
        # XXX This won't work nicely with concrete features, where the
        # utterance will have multiple words as one -- the whole phrase
        # abstracted from.
        if startidx == -1:
            return self
        else:
            # If any replacement took place, reflect it in self._utterance and
            # self._abstr_idxs.
            ab_replaced = AbstractedUtterance.from_utterance(replaced)
            shift = 1 - len(orig)  # the replaced element is now one word
            inserted_new = False
            for idx in self._abstr_idxs:
                # If that word was affected by the replacement,
                if idx >= startidx:
                    # Make sure the index of the newly replaced phrase is
                    # inserted into its place.
                    if not inserted_new:
                        ab_replaced._abstr_idxs.append(startidx)
                        inserted_new = True
                    # Unless that word was replaced away itself,
                    if idx > startidx + len(orig):
                        # Note its new index.
                        ab_replaced._abstr_idxs.append(idx + shift)
                else:
                    ab_replaced._abstr_idxs.append(idx)
            # Make sure the index of the newly replaced phrase has been
            # inserted into its place.
            if not inserted_new:
                ab_replaced._abstr_idxs.append(startidx)
            self._abstr_idxs = ab_replaced._abstr_idxs
        return ab_replaced

# Helper methods for the Abstracted class.
AbstractedUtterance.replace_typeval = AbstractedUtterance.replace


class UtteranceFeatures(Features):
    """Represents the vector of features for an utterance.

    The class also provides methods for manipulation of the feature vector,
    including extracting features from an utterance.

    Currently, only n-gram (including skip n-grams) features are implemented.

    Attributes:
        type: type of features ('ngram')
        size: size of features (an integer)
        features: mapping { feature : value of feature (# occs) }

    """
    def __init__(self, type='ngram', size=3, utterance=None):
        """Creates a vector of utterance features if `utterance' is provided.
        Otherwise, just saves the type and size of features requested.

        Keyword arguments:
            - type: the type of features as a string; currently only 'ngram' is
                implemented
            - size: maximum order of the (n-gram) features.  For skip n-grams,
                this is the distance between the first and last word plus one.
                Moreover, skip n-grams are considered only up to the length 4.
            - utterance: the utterance for which to extract the features;
                If utterance is None (the default), an all-zeroes vector is
                created.

                Otherwise, utterance must be an instance of Utterance.

        """
        # This initialises the self.features field.
        super(UtteranceFeatures, self).__init__()

        self.type = type
        self.size = size

        if utterance is not None:
            self.parse(utterance)

    def parse(self, utterance, with_boundaries=True):
        """Extracts the features from `utterance'."""
        if utterance.isempty():
            self.features['__empty__'] += 1.0
        elif self.type == 'ngram':
            # Compute shorter n-grams.
            for word in utterance:
                self.features[(word, )] += 1.
            if self.size >= 2:
                for ngram in utterance.iter_ngrams(2, with_boundaries=with_boundaries):
                    self.features[tuple(ngram)] += 1.
            # Compute n-grams and skip n-grams for size 3.
            if self.size >= 3:
                for ngram in utterance.iter_ngrams(3, with_boundaries=with_boundaries):
                    self.features[tuple(ngram)] += 1.
                    self.features[(ngram[0], '*1', ngram[2])] += 1.
            # Compute n-grams and skip n-grams for size 4.
            if self.size >= 4:
                for ngram in utterance.iter_ngrams(4, with_boundaries=with_boundaries):
                    self.features[tuple(ngram)] += 1.
                    self.features[(ngram[0], '*2', ngram[3])] += 1.
            # Compute longer n-grams.
            for length in xrange(5, self.size + 1):
                for ngram in utterance.iter_ngrams(length,
                                                   with_boundaries=with_boundaries):
                    self.features[tuple(ngram)] += 1.
        else:
            raise NotImplementedError(
                "Features can be extracted only from an empty utterance or "
                "for the `ngrams' feature type.")
        # FIXME: This is a debugging behaviour. Condition on DEBUG or `verbose'
        # etc. or raise it as an exception.
        if len(self.features) == 0:
            print '(EE) Utterance with no features: "{utt}"'.format(
                utt=utterance.utterance)


class UtteranceHyp(ASRHypothesis):
    """Provide an interface for 1-best hypothesis from the ASR component."""
    def __init__(self, prob=None, utterance=None):
        self.prob = prob
        self.utterance = utterance

    def __str__(self):
        return "%.3f %s" % (self.prob, self.utterance)

    def get_best_utterance(self):
        return self.utterance


class UtteranceNBList(ASRHypothesis, NBList):
    """Provides functionality of n-best lists for utterances.

    When updating the n-best list, one should do the following.

    1. add utterances or parse a confusion network
    2. merge and normalise, in either order

    Attributes:
        n_best: the list containing pairs [prob, utterance] sorted from the
                most probable to the least probable ones

    """
    def __init__(self):
        NBList.__init__(self)

    def get_best_utterance(self):
        """Returns the most probable utterance.

        DEPRECATED. Use get_best instead.

        """
        return self.get_best()

    def get_best(self):
        if self.n_best[0][1] == '__other__':
            return self.n_best[1][1]
        return self.n_best[0][1]

    # TODO Replace with NBList.normalise.
    def scale(self):
        """Scales the n-best list to sum to one."""
        return NBList.normalise(self)

    def normalise(self):
        """The N-best list is extended to include the "__other__" utterance to
        represent those utterance hypotheses which are not included in the
        N-best list.

        DEPRECATED. Use add_other instead.

        """
        return self.add_other()

    def add_other(self):
        try:
            return NBList.add_other(self, Utterance('__other__'))
        except NBListException as ex:
            # DEBUG
            import ipdb; ipdb.set_trace()
            raise UtteranceNBListException(ex)

    # XXX It is now a class invariant that the n-best list is sorted.
    def sort(self):
        """DEPRECATED, going to be removed."""
        return self
        # self.n_best.sort(reverse=True)


class UtteranceNBListFeatures(Features):
    # TODO Document.
    def __init__(self, type='ngram', size=3, utt_nblist=None):
        # This initialises the self.features field.
        super(UtteranceNBListFeatures, self).__init__()

        self.type = type
        self.size = size

        if utt_nblist is not None:
            self.parse(utt_nblist)

    def parse(self, utt_nblist):
        """This should be called only once during the object's lifetime,
        preferrably from within the initialiser.
        """
        first_utt_feats = None
        for hyp_idx, hyp in enumerate(utt_nblist):
            prob, utt = hyp
            utt_feats = UtteranceFeatures(type=self.type,
                                          size=self.size,
                                          utterance=utt)
            if first_utt_feats is None:
                first_utt_feats = utt_feats
            for feat, feat_val in utt_feats.iteritems():
                # Include the first rank of features occurring in the n-best list.
                if (0, feat) not in self.features:
                    self.features[(0, feat)] = float(hyp_idx)
                # Include the weighted features of individual hypotheses.
                self.features[(1, feat)] += prob * feat_val
        # Add features of the top utterance
        if first_utt_feats is None:
            self.features[(2, None)] = 1.
        else:
            self.features[(2, 'prob')] = utt_nblist[0][0]
            for feat, feat_val in first_utt_feats.iteritems():
                self.features[(2, feat)] = feat_val


# TODO Abstract to ml.features.ConfusionNetwork (not a typed one).
# TODO Make UtteranceConfusionNetwork inherit from Abstracted. It is already
# handled in dailrclassifier._extract_feats_from_one.  Once the class is
# defined, try providing input from ASR in the form of the
# AbstractedConfusionNetwork, extracting features from its instantiations and
# subsequently processing these as usual.
class UtteranceConfusionNetwork(ASRHypothesis, Abstracted):
    """Word confusion network

    Attributes:
        cn: a list of alternatives of the following signature
            [word_index-> [ alternative ]]

    XXX Are the alternatives always sorted wrt their probability in
    a decreasing order?

    TODO Define a lightweight class SimpleHypothesis as a tuple (probability,
    fact) with easy-to-read indexing. namedtuple might be the best choice.

    """
    other_val = ('[OTHER]', )

    LongLink = namedtuple('LongLink', ['end', 'orig_probs', 'hyp'])
    # TODO Document.

    def __init__(self):
        self._abstr_idxs = list()  # :: [ (word idx, alt idx) ]
                                   # sorted in the increasing order
        self._cn = []
        self._wordset = set()
        self._long_links = list()  # :: [word_idx-> [ long_link ]]
        #   where long_link describes a link in the confnet from word_idx to an
        #   index larger than (word_idx + 1), and is represented as follows:
        #       long_link :: (end_idx, orig_probs, (prob, phrase))
        #   See the LongLink definition above.
        ASRHypothesis.__init__(self)
        Abstracted.__init__(self)

    def __str__(self):
        return '\n'.join(' '.join('({p:.3f}: {w})'.format(p=hyp[0], w=hyp[1])
                                  for hyp in alts)
                         + ' ' +
                         ' '.join('[{len_} ({p:.3f}: {phr})]'.format(
                               len_=link.end - start,
                               p=link.hyp[0],
                               phr=' '.join(link.hyp[1]))
                           for link in self._long_links[start])
                         for (start, alts) in enumerate(self._cn))

    def __contains__(self, phrase):
        return self.find(phrase) != -1

    def __len__(self):
        return len(self._cn)

    def __getitem__(self, i):
        return self._cn[i]

    def __iter__(self):
        for alts in self._cn:
            yield alts

    @property
    def cn(self):
        return self._cn

    # Abstracted implementations.
    @classmethod
    def make_other(cls, type_):
        return (u'{t}-OTHER'.format(t=type_[0]), )

    def iter_typeval(self):
        for widx, altidx in self._abstr_idxs:
            if widx >= 0:
                yield (self._cn[widx][altidx][1], )
            else:
                yield (self._long_links[-widx][altidx].hyp[1], )

    def join_typeval(self, type_, val):
        return (self.splitter.join((type_[0], ' '.join(val))), )

    def replace_typeval(self, combined, replacement):
        replaced, repl_idxs = self._replace(combined, replacement)
        replaced._abstr_idxs = [idx for idx in replaced._abstr_idxs
                                if idx + (0, ) not in repl_idxs]
        return replaced

    def iter_triples(self):
        for combined_el, in self.iter_typeval():
            split = combined_el.split(self.splitter, 2)
            try:
                type_, value = split
            except ValueError:
                value = ''
                type_ = split[0] if combined_el else ''
            # XXX Change the order of return values to combined_el, type_,
            # value.
            yield (combined_el, ), tuple(value.split(' ')), (type_, )

    # Methods to support preprocessing.
    def lower(self):
        """Lowercases words of this confnet.

        BEWARE, this method is destructive, it lowercases self.

        """
        for widx, alts in enumerate(self._cn):
            self._cn[widx] = [(hyp[0], hyp[1].lower()) for hyp in alts]
        self._wordset = set(word.lower() for word in self._wordset)
        return self

    def replace(self, phrase, replacement):
        replaced, repl_idxs = self._replace(phrase, replacement)
        return replaced

    def phrase2category_label(self, phrase, catlab):
        """Replaces the phrase given by `phrase' by a new phrase, given by
        `catlab'.  Assumes `catlab' is an abstraction for `phrase'.

        """
        combined_el = self.splitter.join((' '.join(catlab),
                                          ' '.join(phrase)))
        replaced, repl_idxs = self._replace(phrase, (combined_el, ))
        if repl_idxs:
            replaced._abstr_idxs.extend((idx[0], idx[1]) for idx in repl_idxs)
            replaced._abstr_idxs.sort()
        return replaced

    # Other methods.
    def isempty(self):
        return not self._cn and not any(self._long_links)

    def index(self, phrase, start=0, end=None):
        index = self.find(phrase)
        if index == -1:
            raise ValueError('Missing "{phrase}" in "{cn}"'
                             .format(phrase=" ".join(phrase), cn=self._cn))
        return index

    def find_unaware(self, phrase, start=0, end=None):
        # Boil out early if any of phrase's words are not present.
        if not all(word in self._wordset for word in phrase):
            # No match found.
            return -1

        if end is None:
            end = len(self._cn)

        states = [True] + [False] * len(phrase)  # :: [n_words-> does a prefix
            #     of `phrase' of length n end in `self' at the current
            #     position?]
        for widx, alts in enumerate(self._cn[start:end], start=start):
            new_states = [True] + [False] * len(phrase)
            alt_words = map(itemgetter(1), alts)
            for phr_widx, state in enumerate(states):
                if state and phrase[phr_widx] in alt_words:
                    new_states[phr_widx + 1] = True
            if new_states[-1]:
                return widx - len(phrase) + 1
            states = new_states
        if states[-1]:
            return (end - len(phrase))
        else:
            # No match found.
            return -1

    # XXX This is inefficient, but can cope with long links.
    # TODO Test.
    def find(self, phrase, start=0, end=None):
        idxs = self.get_phrase_idxs(phrase, start, end)
        if idxs:
            return abs(idxs[0][0])
        else:
            return -1

    # TODO Test.
    # TODO Implement the option to keep the original value, just adding the
    # replacement by its side.
    def _replace(self, phrase, replacement, keep=False):
        """A private method implementing replacement of phrases.

        Arguments:
            phrase -- what to replace (a list of words)
            replacement -- what to replace with (a list of words, may be empty)
            keep -- if set to True, the original phrase will be kept in the
                confnet

        """
        # Initialise.
        replaced = self
        repl_idxs = None
        # Try to find the first occurrence.
        repl_idxs = list()
        idxs = self.get_phrase_idxs(phrase, start=0)
        if idxs:
            replaced = copy.deepcopy(self)

        do_normalise = update_wordset = False
        # Iterate over occurrences.
        while idxs:
            start_widx = abs(idxs[0][0])
            end_widx, end_aidx, end_lsidx = idxs[-1]
            repl_idxs.extend(idxs)

            # Deletion:
            if len(replacement) == 0:
                if not keep:
                    for widx, aidx, lsidx in idxs:
                        if widx >= 0:
                            del replaced._cn[widx][aidx]
                        elif lsidx == 0:
                            del replaced._long_links[-widx][aidx]
                        else:
                            del replaced._long_links[-widx][aidx][lsidx:]
                    do_normalise = update_wordset = True
            # Substituting in-place:
            elif len(idxs) == len(replacement) == 1 and idxs[0][0] >= 0:
                widx, aidx, lsidx = idxs[0]
                repl_hyp = (replaced._cn[widx][aidx][0], replacement[0])
                if keep:
                    replaced._cn[widx].append(repl_hyp)
                else:
                    replaced._cn[widx][aidx] = repl_hyp
                update_wordset = True
            # Substituting just a part of a long link:
            elif end_lsidx:
                assert len(idxs) == 1  # Anything else would mean skipping
                                       # some words.
                replaced._long_links[-end_widx][end_aidx][end_lsidx:] = (
                    replacement)
                update_wordset = True
            # General case:
            else:
                # Compute the phrase probability.
                orig_probs = [(replaced._cn[widx][aidx][0] if widx >= 0 else
                               replaced._long_links[-widx][aidx].hyp[0])
                              for (widx, aidx, lsidx) in idxs]
                prob = reduce(mul, orig_probs, 1.)
                # Construct the new hypothesis.
                new_hyp = (prob, replacement)
                if end_widx >= 0:  # really a word index
                    # Point to after that word.
                    end = end_widx + 1
                else:  # index to a long link
                    end = replaced._long_links[-end_widx][idxs[-1][1]].end
                new_link = UtteranceConfusionNetwork.LongLink(
                    end, orig_probs, new_hyp)
                replaced._long_links[start_widx].append(new_link)
                # Remove the old hypothesis.
                if not keep:
                    for widx, aidx, lsidx in idxs:
                        if widx >= 0:
                            del replaced._cn[widx][aidx]
                        else:
                            del replaced._long_links[-widx][aidx]
                do_normalise = update_wordset = True

            # Try to find another occurrence.
            idxs = replaced.get_phrase_idxs(phrase, start=start_widx + 1)

        if do_normalise:
            replaced.normalise()

        if update_wordset:
            replaced._wordset = set(hyp[1] for alts in replaced._cn
                                    for hyp in alts)
            replaced._wordset.update(word for links in replaced._long_links
                                     for link in links
                                     for word in link.hyp[1])

        return replaced, repl_idxs

    def add(self, hyps):
        """Adds a new arc to the confnet with alternatives as specified.

        Arguments:
            - hyps: an iterable of simple hypotheses -- (probability, word)
            tuples

        """
        # Normalise the hyps to be sure.
        normaliser = 1. / sum(hyp[0] for hyp in hyps)
        self._cn.append([(hyp[0] * normaliser, hyp[1]) for hyp in hyps])
        self._long_links.append(list())
        self._wordset.update(hyp[1] for hyp in hyps)

    # FIXME Make this method aware of _long_links.
    def get_best_utterance(self):
        utterance = []
        for alts in self._cn:
            utterance.append(alts[0][1])

        return ' '.join(utterance).strip()

    # FIXME Make this method aware of _long_links.
    def get_best_hyp(self):
        utterance = []
        prob = 1.0
        for alts in self._cn:
            utterance.append(alts[0][1])
            prob *= alts[0][0]

        # FIXME Make an utterance constructor that accepts the sentence already
        # tokenized. Doing it this way may not preserve segmentation into
        # phrases (if they contain whitespace).
        utterance = ' '.join(utterance).strip()
        return (prob, Utterance(utterance))

    # FIXME Make this method aware of _long_links.
    def get_prob(self, hyp_index):
        """Returns a probability of the given hypothesis."""
        return reduce(mul,
                      (alts[altidx][0]
                       for (altidx, alts) in izip(hyp_index, self._cn)),
                      1.)
        # prob = 1.0
        # for i, alts in izip(hyp_index, self._cn):
        #     prob *= alts[i][0]
        #
        # return prob

    # def get_phrase_prob(self, start, phrase):
        # """Returns the probability of a phrase starting at the index `start'.
        # This method adds probabilities for different ways corresponding to the
        # same phrase.
#
        # Arguments:
            # start: where the phrase starts (exactly)
            # phrase: the phrase as a list of words
#
        # """
        # # Handle the special case related to _long_links.
        # prob = 0.
        # for end_idx, (prob, link_phrase) in self._long_links.get(start,
                                                                 # tuple()):
            # if link_phrase == phrase[:len(link_phrase)]:
                # prob += self.get_phrase_prob(end_idx,
                                             # phrase[len(link_phrase):])
#
        # prob += reduce(mul,
                       # (sum(hyp[0] for hyp in alts if hyp[1] == word)
                        # for (word, alts) in izip(phrase, self._cn[start:])),
                       # 1.)
        # return prob

    def get_phrase_idxs(self, phrase, start=0, end=None, start_in_midlinks=True):
        """Returns indexes to words constituting the given phrase within this
        confnet.  It looks only for the first occurrence of the phrase in the
        interval specified.

        Arguments:
            phrase: the phrase to look for, specified as a list of words
            start: the index where to start searching
            end: the index after which to stop searching
            start_in_midlinks: whether a phrase starting in the middle of
                a long link should be considered too

        Returns:
            - an empty list in case that phrase was not found
            - a list of indices to words that constitute that phrase within
              this confnet.  These index tuples consist of (word index,
              alternative index), corresponding to the two dimensions of the
              confnet.  If the word index is negative, that marks this subphrase
              comes from a "long link".  In that case, the negative word index
              marks the start of the phrase, and the alternative index points
              to the list of long links starting in this word index.

              In case the method was called with `start_in_midlinks=True'
              the indices returned are triples, the last element indexing the
              start of the phrase within the long link (or 0 if not applicable).

        """
        # Special case -- searching for an empty phrase.
        if not phrase:
            return [(start, 0, 0)]

        # Special case -- end <= start.
        if end is None:
            end = len(self._cn)
        if end <= start:
            return []

        # Boil out early if any of phrase's words are not present.
        if not all(word in self._wordset for word in phrase):
            return []

        word = phrase[0]
        for start_idx in xrange(start, end):
            # Find matching hypotheses among the regular one-word arcs
            # starting here.
            matching_hyps = [aidx_hyp for aidx_hyp
                             in enumerate(self._cn[start_idx])
                             if aidx_hyp[1][1] == word]
            if matching_hyps:
                if len(phrase) == 1:
                    return [(start_idx, matching_hyps[0][0], 0)]
                sub_find = self.get_phrase_idxs(phrase[1:], start_idx + 1, end,
                                                start_in_midlinks=False)
                if sub_find:
                    return [(start_idx, matching_hyps[0][0], 0)] + sub_find
            # Find matching hypotheses among long links starting here.
            for l_idx, link in enumerate(self._long_links[start_idx]):
                l_len = len(link.hyp[1])
                if l_len == len(phrase):
                    if link.hyp[1] == phrase:
                        return [(-start_idx, l_idx, 0)]
                elif l_len < len(phrase):
                    if link.end <= end and link.hyp[1] == phrase[:l_len]:
                        sub_find = self.get_phrase_idxs(
                            phrase[l_len:], link.end, end,
                            start_in_midlinks=False)
                        if sub_find:
                            return [(-start_idx, l_idx, 0)] + sub_find

        # Check whether the phrase could start in the middle of a long link.
        if start_in_midlinks:
            # Select all long links that belong to the range specified.
            phrase_links = list()
            for start_idx, links in enumerate(self._long_links[start:end],
                                              start=start):
                phrase_links.extend((start_idx, link) for link in links
                                    if link.end > start)
            # Check all possible phrase starts within those links.
            for start_idx, link in phrase_links:
                link_phr = link.hyp[1]
                for link_start_idx in xrange(1, len(link_phr)):
                    l_suffix = link_phr[link_start_idx:]
                    l_len = len(l_suffix)
                    if l_suffix == phrase[:l_len]:
                        sub_find = self.get_phrase_idxs(
                            phrase[l_len:], link.end, end,
                            start_in_midlinks=False)
                        if sub_find:
                            return ([(-start_idx, l_idx, link_start_idx)]
                                    + sub_find)

        return []

    # TODO Test.
    # TODO Implement the option to keep the original value, just adding the
    # replacement by its side.
    def get_next_worse_candidates(self, hyp_index):
        """Returns such hypotheses that will have lower probability. It assumes
        that the confusion network is sorted."""
        worse_hyp = []

        for i in range(len(hyp_index)):
            wh = list(hyp_index)
            wh[i] += 1
            if wh[i] >= len(self._cn[i]):
                # this generate inadmissible word hypothesis
                continue

            worse_hyp.append(tuple(wh))

        return worse_hyp

    # FIXME Fix the typo.
    def get_hyp_index_utterence(self, hyp_index):
        s = [alts[i][1] for i, alts in zip(hyp_index, self._cn)]

        return Utterance(' '.join(s))

    # FIXME Make this method aware of _long_links.
    def get_utterance_nblist(self, n=10, expand_upto_total_prob_mass=0.9):
        """Parses the confusion network and generates N-best hypotheses.

        The result is a list of utterance hypotheses each with a with assigned
        probability.  The list also include the utterance "__other__" for not
        having the correct utterance in the list.
        """
        # print "Confnet:"
        # print self
        # print

        open_hyp = []
        closed_hyp = {}

        # create index for the best hypothesis
        best_hyp = tuple([0] * len(self._cn))
        best_prob = self.get_prob(best_hyp)
        open_hyp.append((best_prob, best_hyp))

        i = 0
        while open_hyp and i < n:
            i += 1

            current_prob, current_hyp_index = open_hyp.pop(0)

            if current_hyp_index not in closed_hyp:
                # process only those hypotheses which were not processed so far

                closed_hyp[current_hyp_index] = current_prob

                # print "current_prob, current_hyp_index:", current_prob,
                # current_hyp_index

                for hyp_index in self.get_next_worse_candidates(
                        current_hyp_index):
                    prob = self.get_prob(hyp_index)
                    open_hyp.append((prob, hyp_index))

                open_hyp.sort(reverse=True)

        nblist = UtteranceNBList()
        for idx in closed_hyp:
            nblist.add(closed_hyp[idx], self.get_hyp_index_utterence(idx))

        # print nblist
        # print

        nblist.merge()
        nblist.normalise()
        nblist.sort()

        # print nblist
        # print

        return nblist

    def merge(self):
        """Adds up probabilities for the same hypotheses.

        TODO: not implemented yet
        """
        return self

    def prune(self, prune_prob=0.001):
        pruned_cn = []
        for alts in self._cn:
            if not alts[0][1] and alts[0][0] > 1.0 - prune_prob:
                # prune out silences
                continue

            pruned_alts = []
            for p, w in alts:
                if p < prune_prob:
                    continue
                else:
                    pruned_alts.append([p, w])

            if pruned_alts[0][1] == "" and len(pruned_alts) == 1:
                # I pruned out all alternatives except for silence,
                # then skip this
                continue

            pruned_cn.append(alts)

        self._cn = pruned_cn
        self._wordset = set(hyp[1] for alts in self._cn for hyp in alts)

    def normalise(self, end=None):
        """Makes sure that all probabilities add up to one.  There should be
        no need of calling this from outside, since this invariant is ensured
        all the time.

        """
        if end == None or end >= len(self._cn):
            cn_iter = self._cn
        else:
            cn_iter = self._cn[:end]

        link_idxs = list()
        for start_idx, alts in enumerate(cn_iter):
            # Move indices into long links one word forward.
            new_link_idxs = list()
            for idx in link_idxs:
                new_lidx = idx[2] + 1
                if new_lidx < len(self._long_links[idx[0]][idx[1]].orig_probs):
                    new_link_idxs.append((idx[0], idx[1], new_lidx))
            link_idxs = new_link_idxs
            # Add indices into long links that start here.
            for aidx in xrange(len(self._long_links[start_idx])):
                link_idxs.append((start_idx, aidx, 0))
            tot = sum(hyp[0] for hyp in self._cn[start_idx])
            tot += sum(self._long_links[widx][aidx].orig_probs[lidx]
                       for (widx, aidx, lidx) in link_idxs)
            normaliser = 1. / tot
            self._cn[start_idx] = [(hyp[0] * normaliser, hyp[1])
                                   for hyp in self._cn[start_idx]]
            for widx, aidx, lidx in link_idxs:
                self._long_links[widx][aidx].orig_probs[lidx] *= normaliser

        # for alts in self._cn:
        #     sum = 0.0
        #     for p, w in alts:
        #         sum += p
        #
        #     for i in range(len(alts)):
        #         alts[i][0] /= sum

    def sort(self):
        """Sort the alternatives for each word according their probability."""
        for alts in self._cn:
            alts.sort(reverse=True)
        return self

    def iter_ngrams_fromto(self, from_=None, to=None):
        """Iterates n-gram hypotheses between the indices `from_' and `to_'.
        This method does not consider phrases longer than 1 that were
        substituted into the confnet.

        """
        cn_splice = self._cn[from_:to]

        options = [xrange(len(alts)) for alts in cn_splice]
        for option_seq in product(*options):
            hyp_seq = [alts[option]
                       for (alts, option) in izip(cn_splice, option_seq)]
            prob = reduce(mul, map(itemgetter(0), hyp_seq), 1.)
            ngram = map(itemgetter(1), hyp_seq)
            yield (prob, ngram)

    def iter_ngrams_unaware(self, n, with_boundaries=False):
        """Iterates n-gram hypotheses of the length specified.  This is the
        interface method, and uses `iter_ngrams_fromto' internally.  This
        method does not consider phrases longer than 1 that were substituted
        into the confnet.

        Arguments:
            n: size of the n-grams
            with_boundaries: whether to include special sentence boundary marks

        """
        min_len = n - with_boundaries * 2
        # If the n-gram so so fits into the confnet.
        if len(self._cn) <= min_len:
            if len(self._cn) == min_len:
                if with_boundaries:
                    for prob, ngram in self.iter_ngrams_fromto():
                        yield (prob, [SENTENCE_START] + ngram + [SENTENCE_END])
                else:
                    for ngram_hyp in self.iter_ngrams_fromto():
                        yield ngram_hyp
            return
        # Usual cases.
        if with_boundaries and len(self._cn) > min_len:
            for prob, ngram in self.iter_ngrams_fromto(0, n - 1):
                yield (prob, [SENTENCE_START] + ngram)
        for start_idx in xrange(len(self._cn) - n + 1):
            for ngram_hyp in self.iter_ngrams_fromto(start_idx, start_idx + n):
                yield ngram_hyp
        if with_boundaries:
            for prob, ngram in self.iter_ngrams_fromto(-(n - 1), None):
                yield (prob, ngram + [SENTENCE_END])

    # XXX This method is not the most efficient possible.  It may call itself
    # recursively with the same arguments several times.
    def iter_ngrams(self, n, with_boundaries=False, start=None):
        """Iterates n-gram hypotheses of the length specified.  This is the
        interface method.  It is aware of multi-word phrases ("long links")
        that were substituted into the confnet.

        Arguments:
            n: size of the n-grams
            with_boundaries: whether to include special sentence boundary marks
            start: at which word index the n-grams have to start (exactly)

        """
        # Find n-gram start indices that shall be iterated over.
        if start is not None:
            if start < len(self._cn):
                start_iter = (start, )
            elif start == len(self._cn) and with_boundaries:
                start_iter = tuple()
            else:
                return
        else:
            start_iter = xrange(len(self._cn))

        if n == 1:
            if with_boundaries and start is None:
                yield (1., [SENTENCE_START])
            for start_idx in start_iter:
                for hyp in self._cn[start_idx]:
                    yield (hyp[0], [hyp[1]])
                for link in self._long_links[start_idx]:
                    if len(link.hyp[1]) == 1:
                        yield link.hyp
                    elif start is not None:
                        yield (link.hyp[0], [link.hyp[1][0]])
                    else:
                        # avg_prob = link.hyp[0] ** (1. / len(link.hyp[1]))
                        for word in link.hyp[1]:
                            yield (link.hyp[0], [word])
            if with_boundaries and start is None or start == len(self._cn):
                yield (1., [SENTENCE_END])
        elif n > 1:
            # Handle n-grams starting at the sentence-start symbol.
            if with_boundaries and start is None:
                for prob, sub_ngram in self.iter_ngrams(n - 1, with_boundaries,
                                                        0):
                    yield (prob, [SENTENCE_START] + sub_ngram)
            # Handle the normal n-grams.
            for start_idx in start_iter:
                here_hyps = self._cn[start_idx]
                sub_hyps = self.iter_ngrams(n - 1, with_boundaries,
                                            start_idx + 1)
                for ((prob, word), (sub_prob, sub_ngram)) in product(here_hyps,
                                                                     sub_hyps):
                    yield (sub_prob * prob, [word] + sub_ngram)
                # Handle long links.
                for link in self._long_links[start_idx]:
                    l_phrase = link.hyp[1]
                    l_len = len(l_phrase)
                    # avg_prob = link.hyp[0] ** (1. / l_len)
                    prob = link.hyp[0]
                    # Output n-grams that fit completely into this long link.
                    # ngram_prob = avg_prob ** n
                    for lsidx in xrange(l_len - n + 1):
                        yield (prob, list(l_phrase[lsidx:lsidx + n]))
                    # Output n-grams that start later in this long link.
                    #
                    # min_start_idx shall denote the minimum start index within
                    # the long link for n-grams that reach at least to the end
                    # of the long link.  The special case of an n-gram that
                    # exactly fits (and has therefore already been output) is
                    # handled specially.
                    if l_len == n:
                        min_startidx = 1
                    else:
                        min_startidx = max(0, l_len - n)

                    end_startidx = l_len if start is None else 1
                    for lsidx in xrange(min_startidx, end_startidx):
                        len_prefix = l_len - lsidx
                        phr_prefix = l_phrase[lsidx:]
                        # prob = avg_prob ** len_prefix
                        # ...This would lead to overly optimistic probabilities
                        # for phrases substituted in the network that consist
                        # of several words.
                        for sub_prob, sub_ngram in self.iter_ngrams(
                                n - len_prefix, with_boundaries, link.end):
                            yield (prob * sub_prob,
                                   list(phr_prefix) + sub_ngram)


# TODO Document.
# TODO Extend to AbstractedLattice.
# TODO Write tests.class UtteranceConfusionNetworkFeatures(Features):
class UtteranceConfusionNetworkFeatures(Features):
    """Represents features extracted from an utterance hypothesis in the form
    of a confusion network.  These are simply a probabilistic generalisation of
    simple utterance features.  Only n-gram (incl. skip n-gram) features are
    currently implemented.

    """

    def __init__(self, type='ngram', size=3, confnet=None):
        """Creates a vector of confnet features if `confnet' is provided.
        Otherwise, just saves the type and size of features requested.

        Keyword arguments:
            - type: the type of features as a string; currently only 'ngram' is
                implemented
            - size: maximum order of the (n-gram) features.  For skip n-grams,
                this is the distance between the first and last word plus one.
                Moreover, skip n-grams are considered only up to the length 4.
            - confnet: the confnet for which to extract the features;
                If confnet is None (the default), an all-zeroes vector is
                created.

                Otherwise, confnet must be an instance of
                UtteranceConfusionNetwork.

        """
        # This initialises the self.features field.
        super(UtteranceConfusionNetworkFeatures, self).__init__()

        self.type = type
        self.size = size

        if confnet is not None:
            self.parse(confnet)

    def parse(self, confnet):
        """Extracts the features from `confnet'."""
        if confnet.isempty():
            self.features['__empty__'] += 1.0
        elif self.type == 'ngram':
            # Compute shorter n-grams.
            for alts in confnet:
                for prob, word in alts:
                    self.features[(word, )] += prob
            if self.size >= 2:
                for prob, ngram in confnet.iter_ngrams(
                        2, with_boundaries=True):
                    self.features[tuple(ngram)] += prob ** .5
            # Compute n-grams and skip n-grams for size 3.
            if self.size >= 3:
                for prob, ngram in confnet.iter_ngrams(
                        3, with_boundaries=True):
                    self.features[tuple(ngram)] += prob ** (1. / 3)
                    self.features[(ngram[0], '*1', ngram[2])] += prob ** .5
            # Compute n-grams and skip n-grams for size 4.
            if self.size >= 4:
                for prob, ngram in confnet.iter_ngrams(
                        4, with_boundaries=True):
                    self.features[tuple(ngram)] += prob ** .25
                    self.features[(ngram[0], '*2', ngram[3])] += prob ** .5
            # Compute longer n-grams.
            for length in xrange(5, self.size + 1):
                for prob, ngram in confnet.iter_ngrams(
                        length, with_boundaries=True):
                    self.features[tuple(ngram)] += prob ** (1. / length)
        else:
            raise NotImplementedError(
                "Features can be extracted only from an empty confnet or "
                "for the `ngrams' feature type.")

        if len(self.features) == 0:
            raise UtteranceConfusionNetworkException(
                    'No features extracted from the confnet:\n{}'.format(
                        confnet))
