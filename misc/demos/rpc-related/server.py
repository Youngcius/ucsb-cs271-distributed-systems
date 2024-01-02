import time

import Pyro5 as pyro
from Pyro5.api import expose, behavior, Daemon


@expose
@behavior(instance_mode="single")
class Warehouse:
    def __init__(self):
        self.contents = ["chair", "bike", "flashlight", "laptop", "couch"]

    def list_contents(self):
        time.sleep(3)
        print("The warehouse contains:", self.contents)
        # return self.contents

    def take(self, name, item):
        self.contents.remove(item)
        print("{0} took the {1}.".format(name, item))

    def store(self, name, item):
        self.contents.append(item)
        print("{0} stored the {1}.".format(name, item))


if __name__ == "__main__":
    with Daemon(host='localhost', port=23000) as daemon:
        # Register class with Pyro
        uri = daemon.register(Warehouse, 'Warehouse')
        # Print the URI of the published object
        print(uri)
        # Start the server event loop
        daemon.requestLoop()
