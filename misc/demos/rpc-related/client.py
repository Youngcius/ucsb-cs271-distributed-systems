import sys
import Pyro5 as pyro
from Pyro5.api import Proxy


class Person(object):
    def __init__(self, name):
        self.name = name

    def list_contents(self, warehouse):
        warehouse.list_contents()
        # print("The warehouse contains:", warehouse.list_contents())

    def visit(self, warehouse):
        print("This is {0}.".format(self.name))
        self.deposit(warehouse)
        self.retrieve(warehouse)
        print("Thank you, come again!")

    def deposit(self, warehouse):
        print("The warehouse contains:", warehouse.list_contents())
        item = input("Type a thing you want to store (or empty): ").strip()
        if item:
            warehouse.store(self.name, item)

    def retrieve(self, warehouse):
        print("The warehouse contains:", warehouse.list_contents())
        item = input("Type something you want to take (or empty): ").strip()
        if item:
            warehouse.take(self.name, item)


warehouse = Proxy("PYRO:Warehouse@localhost:23000")
# janet = Person("Janet")
# henry = Person("Henry")
# janet.visit(warehouse)
# henry.visit(warehouse)

rose = Person("Rose")
rose.list_contents(warehouse)
print("DONE")

