#服务器端代码
import socket
def main():
	#创建服务器端套接字，默认IPV4，TCP协议
	server = socket.socket()
	#获取本主机名
	hostname = socket.gethostname()

	port = 6789
	# print(hostname)
	server.bind((hostname,port))
	#也可以
	# server.bind(('127.0.0.1',port))
	#连接队列大小是5
	server.listen(5)
	while True:
		clientConn, addr = server.accept()
		print('链接地址：%s' % str(addr))
		msg = '服务器发送的消息：----你成功了'
		#发送消息
		clientConn.send(msg.encode('utf-8'))
		msgClient = clientConn.recv(1024).decode('utf-8')
		print('客户端发送的信息：%s ' %msgClient)
		clientConn.close()
if __name__ =="__main__":
	main()
