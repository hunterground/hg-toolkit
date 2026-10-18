require "sinatra"

get "/" do
  content_type :text
  "hello\n"
end
