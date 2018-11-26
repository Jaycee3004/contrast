import json
import numpy as np
import pandas as pd

from bisect import bisect_right
from itertools import combinations, product


def canonical_combination(items, max_length=None):
    """
    Generates all variable-length combinations, given a list.

    Args:
        items (list): collection of objects.
        max_length (int): maximum combination subset length.

    Examples:
        >>> list(canonical_combination([1, 2, 3]))
        [(1,), (2,), (3,), (1, 2), (1, 3), (2, 3), (1, 2, 3)]

        >>> list(canonical_combination(['a', 'b', 'c'], max_length=2))
        [('a',), ('b',), ('c',), ('a', 'b'), ('a', 'c'), ('b', 'c')]

    Yields:
        list: a variable-length combination
    """
    for length in range(1, len(items) + 1):
        for subset in combinations(items, length):
            yield subset
        if length == max_length:
            break


class ContrastSetLearner:
    """
    Executes a data mining algorithm known as contrast-set learning. This
    algorithm is designed to learn association rules that have a statistical
    significant presence in one group over another. In doing so, this learning
    algorithm enables identification of potential indicators that describe
    differences across groups, for example: size = small versus size = large.

    Args:
        frame (DataFrame): pandas DataFrame.
        group_feature (str): feature name to drive contrast-set learning.
        num_parts (int): number of partitions floats shall be split into.
        max_unique_reals (int): number of unique reals to justify partitioning.
        max_rows (int): maximum number of DataFrame records to process.

    Raises:
        ValueError: if `group_feature` does not exist or `num_parts` < 1.
    """
    def __init__(self, frame, group_feature, num_parts=3, max_unique_reals=15,
                 sep='=>', max_rows=None):

        if group_feature not in frame:
            raise ValueError('`contrast_feature` must be a valid column name.')

        if num_parts < 1:
            raise ValueError('`num_parts` must be a positive number.')

        # if so-many rows are desired, select those-many rows
        if max_rows:
            frame = pd.DataFrame(frame.iloc[:max_rows])

        # retrieve discrete features, i.e. categorical and boolean, as object
        subset = frame.select_dtypes(['category', 'bool', 'object'])

        # append the feature to its attribute, making it attribute := value
        for col in subset.columns:
            frame[col] = col + sep + frame[col].astype(str)

        # retrieve continuous features, i.e. float and int, as number
        subset = frame.select_dtypes(['number'])

        # repeat the appending process above, but for real-values
        for col in subset.columns:
            series = frame[col]

            # if numeric feature has many unique values, partition into chunks
            if len(set(series)) > max_unique_reals:
                arr = series.sort_values().unique()
                parts = np.array_split(arr, num_parts)

                # partitions have (lower, upper) value; use lower to get index
                values = list(map(lambda x: (x[0], x[-1]), parts))
                lwr = list(map(lambda x: x[0], values))

                # determine which (lower, upper) range this value falls into
                series = series.apply(lambda x: values[bisect_right(lwr, x)-1])
                frame[col] = col + sep + series.astype(str)

            # if numeric feature has few unique values, append it like object
            else:
                frame[col] = col + sep + frame[col].astype(str)

        # contrasting needs features, i.e. size, and its states, i.e. size => S
        metadata = {}
        for col in frame:

            # add all the states pointing to their features to the metadata
            states = list(frame[col].unique())
            for ix, state in enumerate(states):
                element = {state: {'pos': ix, 'feature': col}}
                metadata.setdefault('states', {}).update(element)

            # add all the features pointing to their states to the metadata
            metadata.setdefault('features', {}).update({col: states})
        metadata.update({'group_feature': group_feature})
        self.metadata = metadata

        # get the contrast group, remove from frame, and make items as one list
        group_values = pd.Series(frame[group_feature])
        frame.drop(group_feature, axis=1, inplace=True)
        items = pd.Series(frame.apply(lambda x: tuple(x), axis=1), name='items')

        # merge group values and items as DataFrame, and count their frequency
        dummy_frame = pd.concat([group_values, items], axis=1)
        counts = dummy_frame.groupby(list(dummy_frame.columns)).size()

        # data is list containing the items, its group, and count
        self.data = counts.reset_index(name='count').to_dict(orient='records')
        self.group = group_feature  # feature to contrast, aka. column name
        self.counts = {}

    def learn(self, max_length=3, max_records=None, shuffle=True, seed=None):

        if shuffle:
            rng = np.random.RandomState(seed)
            rng.shuffle(self.data)

        if max_records:
            self.data = self.data[:max_records]

        # get number of states for the feature
        num_states = len(self.metadata['features'][self.group])

        # we intend, in this block, to compute counts for the rule across groups
        for row_num, rec in enumerate(self.data):
            state, items, count = rec[self.group], rec['items'],rec['count']

            for rule in canonical_combination(items, max_length):
                if rule not in self.counts:
                    self.counts[rule] = np.zeros((2, num_states))

                # update the rule (row 0) count given the column index of state
                contingency_matrix = self.counts[rule]

                # get columnar position of the group state and update matrix
                pos = self.metadata['states'][state]['pos']
                contingency_matrix[0][pos] += count

        # can be thrown if `min_support_count` is too high
        if len(self.counts) == 0:
            raise ValueError('No rules left; add data or adjust arguments.')

        # compute the counts for the not-rule
        for rule in self.counts:

            # given rule, compute all not-rules possibilities
            rule_negations = self.get_rule_negations(rule)
            rule_counts = self.counts[rule]

            # for each not-rule, fetch its counts and add to not-rule (row 1)
            for rule_negated in rule_negations:
                if rule_negated in self.counts:
                    rule_negated_count = self.counts[rule_negated][0]
                    rule_counts[1] += rule_negated_count

        # serves as an upper-bound for how many rules there could be
        return len(self.counts)

    def get_rule_negations(self, rule):
        if not isinstance(rule, tuple) or not len(rule) > 0:
            msg = '`rule` must be tuple; see `self.counts` keys for examples.'
            raise ValueError(msg)

        # stores all not-components, i.e. [size = S, size = L], [height = tall]
        iterables = []

        # for each rule component, fetch its feature, and get all other states
        for component in rule:

            # only rules in the metadata, under states key, are accepted
            if component not in self.metadata['states']:
                raise KeyError(component + " is an invalid rule; see metadata.")

            # fetch the feature given the desired state, or component
            feature = self.metadata['states'][component]['feature']
            all_components = list(self.metadata['features'][feature])

            # remove the rule component, leaving only not-components
            all_components.remove(component)
            iterables.append(all_components)

        # compute negations, i.e. ['a'], ['X', 'Y'] = ['a', 'X'], ['a', 'Y']
        negations = list(product(*iterables))
        return negations

    def score(self, min_support_count=3, min_difference=2):

        # remove low support-count contingency matrices as they are infrequent
        for i, rule in enumerate(list(self.counts)):
            if np.max(self.counts[rule]) <= min_support_count:
                self.counts.pop(rule)
