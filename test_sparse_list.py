from sparse_list import SparseList


def test_sparselist():
    my_list = SparseList()
    assert my_list == []


def test_sparselist_setitem():
    my_list = SparseList()
    assert my_list == []
    my_list[1] = 'foo'
    assert my_list == [None, 'foo']


def test_sparselist_getitem():
    my_list = SparseList()
    assert my_list == []
    assert my_list[5] == None
