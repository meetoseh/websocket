from typing import List


class FenwickTree:
    """Describes a fenwick tree, also known as a binary indexed tree. This
    data structure primarily has two operations: insert and prefix_sum. Insert
    marks an event occurred at a particular index, and prefix_sum returns the
    total number of events that occurred up to a particular index.

    Although this can be thought of as a tree, I don't find it very intuitive
    to think of it that way. First go through the prefix sum algorithm, then
    the insert algorithm. It may be helpful to read the original paper:

    https://static.aminer.org/pdf/PDF/001/073/976/a_new_data_structure_for_cumulative_frequency_tables.pdf

    This implementation has a fixed capacity. It requires O(n) space, where n is
    the capacity, and O(log n) time for insert and prefix_sum operations. It
    only supports capacities which are one less than a power of two.

    This is primarily a reference implementation, as the fenwick trees in use
    by interactive prompts are actually stored in the database to make use of transactions.
    See interactive_prompt_event_fenwick_trees.md in the backend documentation for more details.
    """

    def __init__(self, capacity: int) -> None:
        assert capacity > 0, "capacity must be positive"
        assert (
            (capacity + 1) & capacity
        ) == 0, "capacity must be one less than a power of two"

        self.capacity = capacity
        """The number of distinct indices that can be inserted into."""

        self.tree: List[int] = [0 for _ in range(capacity)]
        """The underlying tree data structure.

        Value interpretation with a capacity of 7:

        1, 1..2, 3, 1..4, 5, 5..6, 7

        where a..b means the sum of events from indices a to b, inclusive.
        whereas just a means the number of events at index a. The pattern
        continues, e.g., for a capacity of 31:

        1, 1..2, 3, 1..4, 5, 5..6, 7, 1..8, 9, 9..10, 11, 9..12, 13, 13..14, 15,
        1..16, 17, 17..18, 19, 17..20, 21, 21..22, 23, 17..24, 25, 25..26, 27,
        25..28, 29, 29..30, 31
        """

    def increment(self, index: int, *, amount: int = 1) -> None:
        """Increments the number of events at the given index by the given
        amount.

        This requires log(n) time, where n is the capacity.
        """
        assert 0 <= index < self.capacity, "index out of bounds"

        one_based_index = index + 1
        while one_based_index <= self.capacity:
            self.tree[one_based_index - 1] += amount
            one_based_index += one_based_index & -one_based_index

    def prefix_sum(self, index: int) -> int:
        """Computes the sum of events that occurred up to the given index,
        inclusive.
        """
        assert 0 <= index < self.capacity, "index out of bounds"

        one_based_index = index + 1
        result = 0
        while one_based_index > 0:
            result += self.tree[one_based_index - 1]
            one_based_index -= one_based_index & -one_based_index
        return result
