struct Student
{
    char* name;
    int num;
    int age;
    float score;
};
void main(){
    int i = 0;
    int count = 0;
    float sum = 0;
    Student sts[4]={{"Li ping", 5, 18, 58.0},
                    {"Wang ming", 6, 18, 61.0},
                    {"Zhang san", 7, 18, 64.1},
                    {"Li si", 8, 18, 65.2}};
    for(; i < 4; i++) {
        if(sts[i].score >= 60.0) {
            count++;
            printf("%s success\n", sts[i].name);
        } else {
            printf("%s fail\n", sts[i].name);
        }
        sum = sum + sts[i].score;
    }
    printf("success count = %d \n", count);
    printf("total score = %.2f \n", sum);
}