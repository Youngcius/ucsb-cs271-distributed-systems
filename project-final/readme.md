## Project final: Distributed Application via Raft

## Theoretical foundation
- https://zinglix.xyz/2020/06/25/raft/
- https://blog.crazytaxii.com/posts/raft/


### Description

Refer to the project [description](./result/Winter23_CS271_Project_final.pdf) for details.

- To start a new client:

    ```shell
    make new
    ```
    or
    ```shell
    make
    ```

- To recover a client:
        
    ```shell
    make recover client=<client_id>
    ```